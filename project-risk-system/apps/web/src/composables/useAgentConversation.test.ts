import { afterEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "@/api/agent";
import { ApiError } from "@/api/http";
import { useAgentConversation } from "@/composables/useAgentConversation";

vi.mock("@/api/agent", () => ({
  agentApi: {
    create: vi.fn(),
    continueConversation: vi.fn(),
    history: vi.fn(),
    confirm: vi.fn(),
    cancelConversation: vi.fn(),
  },
}));

const mockedAgentApi = vi.mocked(agentApi);

/** Minimal localStorage stub backed by an in-memory map. */
function stubLocalStorage(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => store.clear(),
  });
  return store;
}

function envelope(conversationId: string) {
  return {
    conversation: { id: conversationId },
    streamUrl: `/agent/conversations/${conversationId}/events`,
    userMessage: {
      id: `message-${conversationId}`,
      role: "USER",
      content: "有哪些项目？",
      createdAt: "2026-08-17T00:00:00.000Z",
      dataAsOf: null,
      sequence: 1,
    },
  } as never;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

describe("Agent conversation reset", () => {
  it("aborts the active stream and clears every local conversation cursor", async () => {
    mockedAgentApi.create.mockResolvedValue(envelope("old-id"));
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    const send = agent.send("有哪些项目？");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    agent.state.streamingText = "旧回复";
    agent.state.lastEventId = "event-1";
    agent.reset();
    await send;

    expect(agent.state).toMatchObject({
      conversationId: null,
      messages: [],
      status: "idle",
      error: null,
      streamingText: "",
      progress: null,
      lastEventId: null,
    });
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("uses create, rather than continuing the old ID, after reset", async () => {
    mockedAgentApi.create
      .mockResolvedValueOnce(envelope("old-id"))
      .mockResolvedValueOnce(envelope("new-id"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null)));
    const agent = useAgentConversation();

    await agent.send("旧问题");
    expect(agent.state.conversationId).toBe("old-id");
    agent.reset();
    await agent.send("有哪些项目？");

    expect(mockedAgentApi.create).toHaveBeenCalledTimes(2);
    expect(mockedAgentApi.continueConversation).not.toHaveBeenCalled();
    expect(agent.state.conversationId).toBe("new-id");
  });

  it("reconnects a premature EOF from lastEventId without duplicating the assistant", async () => {
    vi.useFakeTimers();
    mockedAgentApi.create.mockResolvedValue(envelope("resume-id"));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          'id: e1\nevent: message.delta\ndata: {"conversationId":"resume-id","messageId":"m-2","sequence":2,"traceId":"t","occurredAt":"2026-08-17T00:00:00.000Z","text":"答复"}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          'id: e2\nevent: completed\ndata: {"conversationId":"resume-id","messageId":"m-2","sequence":3,"traceId":"t","occurredAt":"2026-08-17T00:00:01.000Z","dataAsOf":"2026-08-17T00:00:01.000Z"}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    const sending = agent.send("有哪些高风险？");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await vi.advanceTimersByTimeAsync(1000);
    await sending;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]?.[0]).toContain("after=e1");
    expect(agent.state.messages.filter((item) => item.role === "ASSISTANT")).toHaveLength(1);
    expect(agent.state.status).toBe("completed");
  });
});

describe("Agent conversation restore after refresh", () => {
  it("persists the conversation id on create and restores history from the server", async () => {
    const store = stubLocalStorage();
    mockedAgentApi.create.mockResolvedValue(envelope("persist-id"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null)));
    const agent = useAgentConversation();

    await agent.send("有哪些项目？");
    expect(store.get("risk-platform:agent-conversation-id")).toBe("persist-id");

    // Simulate a page refresh: a fresh composable reads the stored reference.
    const restored = useAgentConversation();
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "persist-id" },
      messages: [
        {
          id: "m-1",
          role: "USER",
          content: "有哪些项目？",
          createdAt: "2026-08-17T00:00:00.000Z",
          dataAsOf: null,
          sequence: 1,
        },
        {
          id: "m-2",
          role: "ASSISTANT",
          content: "共 3 个项目",
          createdAt: "2026-08-17T00:00:01.000Z",
          dataAsOf: null,
          sequence: 2,
        },
      ],
      nextMessageSequence: 3,
    } as never);

    await restored.restore();

    expect(mockedAgentApi.history).toHaveBeenCalledWith("persist-id");
    expect(restored.state.conversationId).toBe("persist-id");
    expect(restored.state.messages).toHaveLength(2);
    expect(restored.state.status).toBe("completed");
    // The next send continues the same conversation rather than creating one.
    mockedAgentApi.continueConversation.mockResolvedValue({
      userMessage: { id: "m-3", role: "USER", content: "第二个", createdAt: "", dataAsOf: null, sequence: 3 },
      streamUrl: "/agent/conversations/persist-id/events",
    } as never);
    const sending = restored.send("第二个");
    await vi.waitFor(() =>
      expect(mockedAgentApi.continueConversation).toHaveBeenCalledWith("persist-id", "第二个"),
    );
    await sending;
  });

  it("does nothing when no conversation reference is stored", async () => {
    stubLocalStorage();
    const agent = useAgentConversation();
    await agent.restore();
    expect(mockedAgentApi.history).not.toHaveBeenCalled();
    expect(agent.state.conversationId).toBeNull();
  });

  it("clears the stale reference when the stored conversation is gone (404)", async () => {
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "gone-id");
    const agent = useAgentConversation();
    mockedAgentApi.history.mockRejectedValue(
      new ApiError("not found", 404, "AGENT_CONVERSATION_NOT_FOUND"),
    );

    await agent.restore();

    expect(mockedAgentApi.history).toHaveBeenCalledWith("gone-id");
    expect(store.get("risk-platform:agent-conversation-id")).toBeUndefined();
    expect(agent.state.conversationId).toBeNull();
    expect(agent.state.status).toBe("idle");
  });

  it("reset clears the stored reference so a new conversation starts empty", async () => {
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "old-id");
    const agent = useAgentConversation();
    agent.reset();
    expect(store.get("risk-platform:agent-conversation-id")).toBeUndefined();
    expect(agent.state.conversationId).toBeNull();
  });
});

describe("Agent conversation dispose vs reset lifecycle", () => {
  it("dispose aborts the stream but keeps the conversation id and persisted reference", async () => {
    const store = stubLocalStorage();
    mockedAgentApi.create.mockResolvedValue(envelope("keep-id"));
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    const send = agent.send("有哪些项目？");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    agent.dispose();
    await send;

    // The live stream is aborted...
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    // ...but the conversation id, persisted reference and visible messages
    // survive so a return to the dashboard rehydrates the same conversation.
    expect(agent.state.conversationId).toBe("keep-id");
    expect(store.get("risk-platform:agent-conversation-id")).toBe("keep-id");
    expect(agent.state.messages).not.toEqual([]);
  });

  it("dispose clears the stream handle so reconnect becomes a no-op", async () => {
    stubLocalStorage();
    mockedAgentApi.create.mockResolvedValue(envelope("noop-id"));
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    const send = agent.send("有哪些项目？");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    agent.dispose();
    await send;

    fetchMock.mockClear();
    await agent.reconnect();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("dispose keeps the reference so a fresh mount restores the same conversation", async () => {
    const store = stubLocalStorage();
    mockedAgentApi.create.mockResolvedValue(envelope("survive-id"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null)));
    const agent = useAgentConversation();
    await agent.send("有哪些项目？");
    agent.dispose();

    // A fresh composable (new mount) reads the reference dispose preserved;
    // reset() would have cleared it and forced a brand-new conversation.
    const restored = useAgentConversation();
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "survive-id" },
      messages: [],
      nextMessageSequence: 2,
    } as never);
    await restored.restore();

    expect(mockedAgentApi.history).toHaveBeenCalledWith("survive-id");
    expect(restored.state.conversationId).toBe("survive-id");
    expect(store.get("risk-platform:agent-conversation-id")).toBe("survive-id");
  });
});

describe("Agent conversation runtime restore (RUNNING / WAITING_FOR_USER)", () => {
  it("restores a RUNNING turn by reattaching the durable execution stream", async () => {
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "running-id");
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "running-id" },
      messages: [
        {
          id: "m-1",
          role: "USER",
          content: "列出风险",
          createdAt: "2026-08-17T00:00:00.000Z",
          dataAsOf: null,
          sequence: 1,
        },
      ],
      runtime: {
        status: "RUNNING",
        streamUrl: "/agent/conversations/running-id/events",
        interaction: null,
        resumeAfterEventId: "event-0",
        resumeAfterEventSequence: 1,
        cancellationRequested: false,
      },
    } as never);
    // The restored stream resumes the SAME execution FROM the snapshot sequence
    // cursor (resumeAfterEventSequence → ?afterSequence=1), then emits the
    // terminal events (a delta + completed) written in the gap between the
    // history snapshot and the SSE GET, so the turn lands without a re-send and
    // the consumer does not reconnect past a clean EOF.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(
        new Response(
          'id: e1\nevent: message.delta\ndata: {"conversationId":"running-id","messageId":"m-2","sequence":2,"traceId":"t","occurredAt":"2026-08-17T00:00:00.000Z","text":"共 2 个高风险"}\n\nid: e2\nevent: completed\ndata: {"conversationId":"running-id","messageId":"m-2","sequence":3,"traceId":"t","occurredAt":"2026-08-17T00:00:01.000Z","dataAsOf":"2026-08-17T00:00:01.000Z"}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );
    const agent = useAgentConversation();

    await agent.restore();

    expect(mockedAgentApi.history).toHaveBeenCalledWith("running-id");
    expect(fetch).toHaveBeenCalledWith(
      "/agent/conversations/running-id/events?afterSequence=1",
      expect.objectContaining({ method: "GET" }),
    );
    expect(agent.state.status).toBe("completed");
    expect(agent.state.messages.filter((m) => m.role === "ASSISTANT")).toHaveLength(1);
    expect(agent.state.streamingText).toBe("");
  });

  it("restores a brand-new RUNNING turn with zero events via ?afterSequence=0 (zero-event race)", async () => {
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "fresh-id");
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "fresh-id" },
      messages: [
        {
          id: "m-1",
          role: "USER",
          content: "列出风险",
          createdAt: "2026-08-17T00:00:00.000Z",
          dataAsOf: null,
          sequence: 1,
        },
      ],
      // A brand-new first turn has written NO AgentEvent yet: the event-id
      // cursor is null and cannot resume, but the sequence cursor is 0.
      runtime: {
        status: "RUNNING",
        streamUrl: "/agent/conversations/fresh-id/events",
        interaction: null,
        resumeAfterEventId: null,
        resumeAfterEventSequence: 0,
        cancellationRequested: false,
      },
    } as never);
    // The worker writes the terminal events in the REST→SSE gap; resuming
    // from ?afterSequence=0 replays them so the answer is not lost.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(
        new Response(
          'id: e1\nevent: message.delta\ndata: {"conversationId":"fresh-id","messageId":"m-2","sequence":1,"traceId":"t","occurredAt":"2026-08-17T00:00:00.000Z","text":"共 2 个高风险"}\n\nid: e2\nevent: completed\ndata: {"conversationId":"fresh-id","messageId":"m-2","sequence":2,"traceId":"t","occurredAt":"2026-08-17T00:00:01.000Z","dataAsOf":"2026-08-17T00:00:01.000Z"}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );
    const agent = useAgentConversation();

    await agent.restore();

    expect(fetch).toHaveBeenCalledWith(
      "/agent/conversations/fresh-id/events?afterSequence=0",
      expect.objectContaining({ method: "GET" }),
    );
    expect(agent.state.status).toBe("completed");
    expect(agent.state.messages.filter((m) => m.role === "ASSISTANT")).toHaveLength(1);
    expect(agent.state.streamingText).toBe("");
  });

  it("restores a WAITING_FOR_USER turn by redisplaying the OPEN interaction card", async () => {
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "waiting-id");
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "waiting-id" },
      messages: [
        {
          id: "m-1",
          role: "USER",
          content: "查询风险",
          createdAt: "2026-08-17T00:00:00.000Z",
          dataAsOf: null,
          sequence: 1,
        },
      ],
      runtime: {
        status: "WAITING_FOR_USER",
        streamUrl: null,
        interaction: {
          id: "interaction-1",
          type: "PROJECT_SELECTION",
          status: "OPEN",
          conversationId: "waiting-id",
          executionId: "exec-1",
          candidates: [{ name: "南岸项目" }],
          draft: null,
          expiresAt: "2026-08-17T00:30:00.000Z",
        },
      },
    } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    await agent.restore();

    // A paused turn re-displays the card; no stream is opened and the user
    // resolves the interaction (PROJECT_SELECTION / WRITE_CONFIRMATION) rather
    // than re-typing.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(agent.state.status).toBe("completed");
    expect(agent.state.interaction).not.toBeNull();
    expect(agent.state.interaction?.id).toBe("interaction-1");
    expect(agent.state.interaction?.type).toBe("PROJECT_SELECTION");
    expect(agent.state.interaction?.status).toBe("OPEN");
    expect(agent.state.interaction?.candidates?.[0]).toMatchObject({ name: "南岸项目" });
  });

  it("leaves a COMPLETED turn as-is when no runtime is present", async () => {
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "done-id");
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "done-id" },
      messages: [
        {
          id: "m-1",
          role: "USER",
          content: "列出风险",
          createdAt: "2026-08-17T00:00:00.000Z",
          dataAsOf: null,
          sequence: 1,
        },
        {
          id: "m-2",
          role: "ASSISTANT",
          content: "共 2 个",
          createdAt: "2026-08-17T00:00:01.000Z",
          dataAsOf: null,
          sequence: 2,
        },
      ],
      runtime: null,
    } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    await agent.restore();

    // COMPLETED / FAILED / CANCELLED / none — the turn is final and the
    // restored messages are the source of truth; no stream, no interaction.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(agent.state.status).toBe("completed");
    expect(agent.state.interaction).toBeNull();
    expect(agent.state.messages).toHaveLength(2);
  });
});

describe("Agent conversation explicit cancel", () => {
  it("aborts the live stream and waits for the worker to reach terminal before completing", async () => {
    stubLocalStorage();
    mockedAgentApi.create.mockResolvedValue(envelope("cancel-id"));
    mockedAgentApi.cancelConversation.mockResolvedValue({
      status: "RUNNING",
      streamUrl: null,
      interaction: null,
    } as never);
    // The cancel POST returns while the execution is still RUNNING; the worker
    // observes the flag asynchronously, so history.runtime reports RUNNING once
    // then goes inactive.  cancel must NOT flip to completed until that happens.
    mockedAgentApi.history
      .mockResolvedValueOnce({
        conversation: { id: "cancel-id" },
        messages: [
          { id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 },
        ],
        runtime: { status: "RUNNING", streamUrl: null, interaction: null },
      } as never)
      .mockResolvedValueOnce({
        conversation: { id: "cancel-id" },
        messages: [
          { id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 },
        ],
        runtime: null,
      } as never);
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    const sending = agent.send("列出风险");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await agent.cancel();
    await sending;

    expect(mockedAgentApi.cancelConversation).toHaveBeenCalledWith("cancel-id");
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    // Polled once while RUNNING, then again at terminal — completed only then.
    expect(mockedAgentApi.history).toHaveBeenCalledTimes(2);
    expect(agent.state.status).toBe("completed");
    expect(agent.state.streamingText).toBe("");
  });

  it("blocks an immediate next send while the worker is still RUNNING, then recovers sendable at terminal", async () => {
    stubLocalStorage();
    mockedAgentApi.create.mockResolvedValue(envelope("cancel-block-id"));
    mockedAgentApi.cancelConversation.mockResolvedValue({
      status: "RUNNING",
      streamUrl: null,
      interaction: null,
    } as never);
    mockedAgentApi.continueConversation.mockResolvedValue({
      userMessage: {
        id: "m-next",
        role: "USER",
        content: "下一条消息",
        createdAt: "",
        dataAsOf: null,
        sequence: 2,
      },
      streamUrl: "/agent/conversations/cancel-block-id/events",
    } as never);
    // history reports RUNNING on the first poll (worker has not yet observed
    // the cancel flag) then inactive on the second — the window between them
    // is when an impatient next send must be blocked instead of surfacing 409.
    mockedAgentApi.history
      .mockResolvedValueOnce({
        conversation: { id: "cancel-block-id" },
        messages: [
          { id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 },
        ],
        runtime: { status: "RUNNING", streamUrl: null, interaction: null },
      } as never)
      .mockResolvedValueOnce({
        conversation: { id: "cancel-block-id" },
        messages: [
          { id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 },
        ],
        runtime: null,
      } as never);
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    const sending = agent.send("列出风险");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());

    // cancel flips to "cancelling" synchronously; the worker is still RUNNING,
    // so an immediate next send must be blocked at the guard — NOT dispatched
    // to continueConversation, which the still-live execution answers 409.
    const cancelPromise = agent.cancel();
    await vi.waitFor(() => expect(mockedAgentApi.history).toHaveBeenCalledOnce());
    expect(agent.state.status).toBe("cancelling");
    await agent.send("下一条消息");
    expect(mockedAgentApi.continueConversation).not.toHaveBeenCalled();

    // Once the worker reaches terminal, cancel completes and the input is
    // sendable again — a subsequent send dispatches to continueConversation.
    await vi.waitFor(() => expect(agent.state.status).toBe("completed"));
    const nextSend = agent.send("下一条消息");
    await vi.waitFor(() =>
      expect(mockedAgentApi.continueConversation).toHaveBeenCalledWith(
        "cancel-block-id",
        "下一条消息",
      ),
    );
    agent.dispose();
    await Promise.allSettled([sending, cancelPromise, nextSend]);
  });

  it("does not call the cancel endpoint when no conversation is active", async () => {
    stubLocalStorage();
    const agent = useAgentConversation();

    await agent.cancel();

    expect(mockedAgentApi.cancelConversation).not.toHaveBeenCalled();
    expect(agent.state.status).toBe("idle");
  });

  it("stays cancelling past the 15s fast deadline while the worker is still RUNNING, then auto-reconciles at terminal", async () => {
    vi.useFakeTimers();
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "slow-cancel-id");
    // restore (call 1) loads a COMPLETED turn; the subsequent cancel fast-poll
    // (calls 2+) reports RUNNING until the worker terminates.
    let workerTerminal = false;
    let historyCalls = 0;
    mockedAgentApi.history.mockImplementation(async () => {
      historyCalls += 1;
      if (historyCalls === 1) {
        return {
          conversation: { id: "slow-cancel-id" },
          messages: [{ id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 }],
          runtime: null,
        } as never;
      }
      return {
        conversation: { id: "slow-cancel-id" },
        messages: [{ id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 }],
        runtime: workerTerminal
          ? null
          : { status: "RUNNING", streamUrl: null, interaction: null, cancellationRequested: true },
      } as never;
    });
    mockedAgentApi.cancelConversation.mockResolvedValue({
      status: "RUNNING",
      streamUrl: null,
      interaction: null,
      cancellationRequested: true,
    } as never);
    mockedAgentApi.continueConversation.mockResolvedValue({
      userMessage: { id: "m-next", role: "USER", content: "下一条", createdAt: "", dataAsOf: null, sequence: 2 },
      streamUrl: "/agent/conversations/slow-cancel-id/events",
    } as never);
    vi.stubGlobal("fetch", vi.fn());
    const agent = useAgentConversation();

    await agent.restore();
    expect(agent.state.status).toBe("completed");

    const cancelPromise = agent.cancel();
    // Drain the fast 15 s deadline with the worker still RUNNING: the input
    // must NOT be released (no premature flip to completed) — it stays 取消中.
    await vi.advanceTimersByTimeAsync(15_500);
    expect(agent.state.status).toBe("cancelling");
    // An immediate next send is blocked at the guard — no continueConversation
    // dispatch against the still-RUNNING execution (it would surface a 409).
    await agent.send("下一条");
    expect(mockedAgentApi.continueConversation).not.toHaveBeenCalled();

    // The worker later terminates; the slow drain reconciles to completed and
    // releases the input.
    workerTerminal = true;
    await vi.advanceTimersByTimeAsync(2_500);
    expect(agent.state.status).toBe("completed");
    await cancelPromise;
  });

  it("restores to cancelling when the runtime reports cancellationRequested (refresh during cancel)", async () => {
    vi.useFakeTimers();
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "refresh-cancel-id");
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "refresh-cancel-id" },
      messages: [{ id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 }],
      runtime: {
        status: "RUNNING",
        streamUrl: "/agent/conversations/refresh-cancel-id/events",
        interaction: null,
        resumeAfterEventId: null,
        resumeAfterEventSequence: 0,
        cancellationRequested: true,
      },
    } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const agent = useAgentConversation();

    await agent.restore();

    // A refresh during an explicit cancel does NOT reopen the normal stream
    // and does NOT re-enable the input — it stays 取消中 and drains.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(agent.state.status).toBe("cancelling");
    // An immediate send is blocked while the turn is still draining.
    await agent.send("下一条");
    expect(mockedAgentApi.continueConversation).not.toHaveBeenCalled();
    agent.dispose();
  });

  it("surfaces an error (not completed) when the cancel POST itself fails", async () => {
    const store = stubLocalStorage();
    store.set("risk-platform:agent-conversation-id", "fail-cancel-id");
    mockedAgentApi.history.mockResolvedValue({
      conversation: { id: "fail-cancel-id" },
      messages: [{ id: "m-1", role: "USER", content: "列出风险", createdAt: "", dataAsOf: null, sequence: 1 }],
      runtime: null,
    } as never);
    mockedAgentApi.cancelConversation.mockRejectedValue(
      new ApiError("cancel forbidden", 403, "AGENT_FORBIDDEN"),
    );
    vi.stubGlobal("fetch", vi.fn());
    const agent = useAgentConversation();

    await agent.restore();
    expect(agent.state.status).toBe("completed");

    await agent.cancel();

    // The cancel request failed → error, NOT completed (the input must not be
    // released as if the turn ended cleanly).
    expect(agent.state.status).toBe("error");
    expect(agent.state.error?.code).toBe("AGENT_FORBIDDEN");
    expect(mockedAgentApi.cancelConversation).toHaveBeenCalledWith("fail-cancel-id");
  });
});
