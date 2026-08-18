import { describe, expect, it } from "vitest";

import {
  agentErrorLabel,
  applyFrame,
  initialAgentState,
  parseSseFrames,
  type SseFrame,
} from "@/utils/agent-sse";

/** Build a wired SSE frame the way the backend `wire_event` serializes it. */
function frame(
  event: string,
  id: string,
  payload: Record<string, unknown>,
): SseFrame {
  return {
    id,
    event,
    data: JSON.stringify({
      conversationId: "c-1",
      messageId: "m-1",
      sequence: 1,
      traceId: "t-1",
      occurredAt: "2026-08-14T00:00:00.000Z",
      ...payload,
    }),
  };
}

describe("SSE frame parser", () => {
  it("splits complete frames and keeps a partial trailing frame as carry", () => {
    const buffer =
      "id: e1\nevent: progress\ndata: {\"stage\":\"s\",\"message\":\"m\"}\n\n" +
      "id: e2\nevent: message.delta\ndata: {\"text\":\"hel";
    const { frames, carry } = parseSseFrames(buffer);
    expect(frames).toHaveLength(1);
    const first = frames[0]!;
    expect(first.id).toBe("e1");
    expect(first.event).toBe("progress");
    expect(carry).toBe("id: e2\nevent: message.delta\ndata: {\"text\":\"hel");
  });

  it("parses multi-line data and normalizes CRLF terminators", () => {
    const buffer =
      "id: e1\r\nevent: completed\r\ndata: line1\r\ndata: line2\r\n\r\n";
    const { frames } = parseSseFrames(buffer);
    expect(frames).toHaveLength(1);
    const first = frames[0]!;
    expect(first.event).toBe("completed");
    expect(first.data).toBe("line1\nline2");
  });

  it("defaults the event field to message and ignores comments", () => {
    const buffer = ": keepalive\ndata: {}\n\n";
    const { frames } = parseSseFrames(buffer);
    expect(frames).toHaveLength(1);
    const first = frames[0]!;
    expect(first.event).toBe("message");
    expect(first.id).toBeNull();
  });

  it("continues accumulation across chunks via carry", () => {
    const part1 = "id: e1\nevent: progress\ndata: {\"stage\":\"a\"";
    const part2 = ",\"message\":\"b\"}\n\n";
    const first = parseSseFrames(part1);
    expect(first.frames).toHaveLength(0);
    const second = parseSseFrames(first.carry + part2);
    expect(second.frames).toHaveLength(1);
    expect(second.carry).toBe("");
    expect(JSON.parse(second.frames[0]!.data).message).toBe("b");
  });
});

describe("agent event reducer", () => {
  it("restores project selection and resolves it without duplicating the prompt", () => {
    let state = applyFrame(
      initialAgentState(),
      frame("interaction.required", "i1", {
        interactionId: "interaction-1",
        type: "PROJECT_SELECTION",
        candidates: [{ id: "p-1", name: "Alpha 项目" }],
        expiresAt: "2026-08-17T00:30:00.000Z",
      }),
    );
    expect(state.status).toBe("completed");
    expect(state.interaction?.type).toBe("PROJECT_SELECTION");
    expect(state.interaction?.candidates).toHaveLength(1);
    state = applyFrame(state, frame("interaction.resolved", "i2", { interactionId: "interaction-1", action: "SELECT" }));
    expect(state.interaction?.status).toBe("RESOLVED");
  });

  it("keeps write confirmation draft fields and batch candidates displayable", () => {
    const state = applyFrame(
      initialAgentState(),
      frame("interaction.required", "i1", {
        interactionId: "interaction-2",
        type: "WRITE_CONFIRMATION",
        draft: { operation: "RISK_CREATE", title: "回款风险", items: [{ title: "A" }, { title: "B" }] },
        expiresAt: "2026-08-17T00:30:00.000Z",
      }),
    );
    expect(state.interaction?.draft?.title).toBe("回款风险");
    expect(state.interaction?.draft?.items).toHaveLength(2);
  });

  it("accumulates message.delta into streaming text", () => {
    let state = initialAgentState();
    state = applyFrame(state, frame("message.delta", "e1", { text: "Hello" }));
    state = applyFrame(state, frame("message.delta", "e2", { text: " world" }));
    expect(state.streamingText).toBe("Hello world");
    expect(state.status).toBe("streaming");
    expect(state.lastEventId).toBe("e2");
  });

  it("records progress events", () => {
    let state = initialAgentState();
    state = applyFrame(
      state,
      frame("progress", "e1", { stage: "thinking", message: "分析中" }),
    );
    expect(state.progress).toEqual({ stage: "thinking", message: "分析中" });
    expect(state.status).toBe("streaming");
  });

  it("finalizes the assistant message on completed and clears streaming state", () => {
    let state = initialAgentState();
    state.messages = [
      {
        id: "u1",
        role: "USER",
        content: "有哪些高风险？",
        createdAt: "2026-08-14T00:00:00.000Z",
        dataAsOf: null,
        sequence: 1,
      },
    ];
    state = applyFrame(state, frame("message.delta", "e1", { text: "共 " }));
    state = applyFrame(state, frame("message.delta", "e2", { text: "2 项" }));
    state = applyFrame(
      state,
      frame("completed", "e3", { dataAsOf: "2026-08-14T00:00:01.000Z" }),
    );
    expect(state.streamingText).toBe("");
    expect(state.progress).toBeNull();
    expect(state.status).toBe("completed");
    expect(state.messages).toHaveLength(2);
    const assistant = state.messages[1]!;
    expect(assistant.role).toBe("ASSISTANT");
    expect(assistant.content).toBe("共 2 项");
    expect(assistant.dataAsOf).toBe("2026-08-14T00:00:01.000Z");
    expect(assistant.sequence).toBe(2);
  });

  it("records a terminal error and clears streaming state", () => {
    let state = initialAgentState();
    state = applyFrame(state, frame("message.delta", "e1", { text: "部分" }));
    state = applyFrame(
      state,
      frame("error", "e2", {
        code: "AGENT_PROVIDER_UNAVAILABLE",
        message: "AI服务暂时不可用",
        retryable: true,
      }),
    );
    expect(state.status).toBe("error");
    expect(state.streamingText).toBe("");
    expect(state.error).toEqual({
      code: "AGENT_PROVIDER_UNAVAILABLE",
      message: "AI服务暂时不可用",
      retryable: true,
    });
  });

  it("treats heartbeat as a no-op that still advances the cursor", () => {
    let state = initialAgentState();
    state = applyFrame(state, frame("heartbeat", "e1", {}));
    expect(state.status).toBe("idle");
    expect(state.lastEventId).toBe("e1");
  });

  it("advances the resume cursor even for an unparseable frame with an id", () => {
    const state = applyFrame(initialAgentState(), {
      id: "bad",
      event: "message",
      data: "{not json",
    });
    expect(state.lastEventId).toBe("bad");
    expect(state.status).toBe("idle");
  });

  it("is resume-safe: lastEventId tracks the last applied event", () => {
    let state = initialAgentState();
    state = applyFrame(state, frame("progress", "e1", { stage: "s", message: "m" }));
    state = applyFrame(state, frame("message.delta", "e2", { text: "x" }));
    expect(state.lastEventId).toBe("e2");
  });

  it("ignores a replayed event so resume cannot duplicate a terminal message", () => {
    let state = applyFrame(initialAgentState(), frame("message.delta", "e1", { text: "答复" }));
    state = applyFrame(state, frame("completed", "e2", { dataAsOf: "2026-08-14T00:00:01.000Z" }));
    const replayed = applyFrame(state, frame("completed", "e2", { dataAsOf: "2026-08-14T00:00:01.000Z" }));
    expect(replayed.messages).toHaveLength(1);
    expect(replayed.status).toBe("completed");
  });
});

describe("agent display labels", () => {
  it("maps known execution error codes to user-facing text", () => {
    expect(
      agentErrorLabel("AGENT_PROVIDER_UNAVAILABLE", "fallback"),
    ).toBe("AI服务暂时不可用，请稍后重试");
    expect(
      agentErrorLabel("AGENT_REPORT_CATEGORY_STALE", "fallback"),
    ).toBe("风险分类已变更，请重新发起");
    expect(agentErrorLabel("UNKNOWN_CODE", "fallback")).toBe("fallback");
  });

});
