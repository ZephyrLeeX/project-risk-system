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
