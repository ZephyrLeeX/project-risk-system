import { afterEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "@/api/agent";
import { useAgentConversation } from "@/composables/useAgentConversation";

vi.mock("@/api/agent", () => ({
  agentApi: {
    create: vi.fn(),
    continueConversation: vi.fn(),
    confirm: vi.fn(),
  },
}));

const mockedAgentApi = vi.mocked(agentApi);

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
