/**
 * Pure Agent SSE parsing and event reduction (ADRs 0019 / 0028 / 0029 / 0036 / 0037).
 *
 * The FastAPI event stream is typed as `text/event-stream: unknown` in the
 * frozen OpenAPI authority (T032), so this module narrows the wire payload at
 * runtime with typed guards instead of substituting a hand-written contract.
 * No `any` is used: every payload field is validated before it is trusted.
 */

import type { AgentInteraction } from "@/api/agent";

// ---------------------------------------------------------------------------
// SSE frame parsing
// ---------------------------------------------------------------------------

export interface SseFrame {
  /** `id:` field — the durable event UUID used to resume the stream. */
  id: string | null;
  /** `event:` field (defaults to `message` per the SSE spec). */
  event: string;
  /** Joined `data:` lines. */
  data: string;
}

/**
 * Split an accumulating buffer into complete SSE frames.
 *
 * Frames are terminated by a blank line (`\n\n`). Any trailing partial frame
 * is returned as `carry` so the caller can prepend the next chunk. Carriage
 * returns are normalized before splitting.
 */
export function parseSseFrames(buffer: string): {
  frames: SseFrame[];
  carry: string;
} {
  const frames: SseFrame[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  let cursor = 0;
  let boundary = normalized.indexOf("\n\n", cursor);
  while (boundary !== -1) {
    const raw = normalized.slice(cursor, boundary);
    cursor = boundary + 2;
    const frame = parseFrame(raw);
    if (frame) frames.push(frame);
    boundary = normalized.indexOf("\n\n", cursor);
  }
  return { frames, carry: normalized.slice(cursor) };
}

function parseFrame(raw: string): SseFrame | null {
  if (raw === "") return null;
  let id: string | null = null;
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue; // SSE comment / keepalive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1); // spec: one leading space
    if (field === "id") {
      id = value;
    } else if (field === "event") {
      event = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }
  return { id, event, data: dataLines.join("\n") };
}

// ---------------------------------------------------------------------------
// Wire payload narrowing
// ---------------------------------------------------------------------------

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Shared base fields present on every Agent event (`wire_event`). */
interface EventBase {
  conversationId: string;
  messageId: string;
  sequence: number;
  traceId: string;
  occurredAt: string;
  raw: Record<string, unknown>;
}

function parseEventBase(data: string): EventBase | null {
  const parsed = safeJson(data);
  const record = asRecord(parsed);
  if (!record) return null;
  return {
    conversationId: asString(record.conversationId),
    messageId: asString(record.messageId),
    sequence: asNumber(record.sequence),
    traceId: asString(record.traceId),
    occurredAt: asString(record.occurredAt),
    raw: record,
  };
}

function safeJson(data: string): unknown {
  if (data === "") return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

// ---------------------------------------------------------------------------
// Conversation state + reducer
// ---------------------------------------------------------------------------

export interface AgentStreamMessage {
  id: string;
  role: "USER" | "ASSISTANT" | "TOOL";
  content: string;
  createdAt: string;
  dataAsOf: string | null;
  sequence: number;
  structured?: Record<string, unknown> | null;
}

export interface AgentProgressState {
  stage: string;
  message: string;
}

export interface AgentErrorState {
  code: string;
  message: string;
  retryable: boolean;
}

export type AgentConversationStatus =
  | "idle"
  | "loading"
  | "streaming"
  | "completed"
  | "error"
  | "disconnected";

export interface AgentConversationState {
  status: AgentConversationStatus;
  conversationId: string | null;
  messages: AgentStreamMessage[];
  /** Accumulated `message.delta` text for the in-flight assistant turn. */
  streamingText: string;
  progress: AgentProgressState | null;
  /** The one open server-owned interaction, restored from durable SSE facts. */
  interaction: AgentInteraction | null;
  error: AgentErrorState | null;
  /** Last applied event UUID — the resume cursor (`after` query param). */
  lastEventId: string | null;
}

export function initialAgentState(): AgentConversationState {
  return {
    status: "idle",
    conversationId: null,
    messages: [],
    streamingText: "",
    progress: null,
    interaction: null,
    error: null,
    lastEventId: null,
  };
}

/**
 * Apply one parsed SSE frame to the conversation state.
 *
 * The reducer is resume-safe: `lastEventId` is updated for every applied
 * frame, so reconnecting with `after=lastEventId` only replays events the
 * client has not yet seen.
 */
export function applyFrame(
  state: AgentConversationState,
  frame: SseFrame,
): AgentConversationState {
  const base = parseEventBase(frame.data);
  // An unparseable frame still advances the resume cursor if it carried an id.
  if (base === null) {
    return frame.id
      ? { ...state, lastEventId: frame.id }
      : state;
  }

  const next: AgentConversationState = {
    ...state,
    lastEventId: frame.id ?? state.lastEventId,
  };

  switch (frame.event) {
    case "progress": {
      next.progress = {
        stage: asString(base.raw.stage),
        message: asString(base.raw.message),
      };
      next.status = streamingOr(state);
      return next;
    }
    case "message.delta": {
      next.streamingText = state.streamingText + asString(base.raw.text);
      next.status = streamingOr(state);
      return next;
    }
    case "interaction.required": {
      const type = asString(base.raw.type);
      const candidates = Array.isArray(base.raw.candidates)
        ? base.raw.candidates.filter((item): item is Record<string, unknown> => asRecord(item) !== null)
        : [];
      next.interaction = {
        id: asString(base.raw.interactionId),
        type,
        status: "OPEN",
        conversationId: base.conversationId,
        executionId: asString(base.raw.executionId),
        candidates: candidates as AgentInteraction["candidates"],
        draft: asRecord(base.raw.draft) as AgentInteraction["draft"],
        expiresAt: asString(base.raw.expiresAt),
      };
      next.status = "completed";
      return next;
    }
    case "interaction.resolved": {
      if (next.interaction && asString(base.raw.interactionId) === next.interaction.id) {
        next.interaction = { ...next.interaction, status: "RESOLVED" };
      }
      next.status = streamingOr(state);
      return next;
    }
    case "completed": {
      const finalized = finalizeAssistant(state, base);
      next.messages = finalized.messages;
      next.streamingText = "";
      next.progress = null;
      next.status = "completed";
      return next;
    }
    case "error": {
      next.error = {
        code: asString(base.raw.code),
        message: asString(base.raw.message, "AI服务暂时不可用"),
        retryable: asBoolean(base.raw.retryable),
      };
      next.streamingText = "";
      next.progress = null;
      next.status = "error";
      return next;
    }
    case "heartbeat":
    default:
      return next;
  }
}

/** A non-terminal event means the turn is streaming, unless it already ended. */
function streamingOr(state: AgentConversationState): AgentConversationStatus {
  return state.status === "completed" || state.status === "error"
    ? state.status
    : "streaming";
}

/** Promote accumulated `message.delta` text into a finalized assistant message. */
function finalizeAssistant(
  state: AgentConversationState,
  base: EventBase,
): { messages: AgentStreamMessage[] } {
  const text = state.streamingText;
  const lastSequence = state.messages.reduce(
    (max, message) => Math.max(max, message.sequence),
    0,
  );
  const assistant: AgentStreamMessage = {
    id: base.messageId,
    role: "ASSISTANT",
    content: text,
    createdAt: base.occurredAt,
    dataAsOf: nullableString(base.raw.dataAsOf),
    sequence: lastSequence + 1,
  };
  return { messages: [...state.messages, assistant] };
}

// ---------------------------------------------------------------------------
// Display labels
// ---------------------------------------------------------------------------

const AGENT_ERROR_LABELS: Record<string, string> = {
  AGENT_PROVIDER_INVALID_OUTPUT: "AI服务返回内容不符合Agent协议",
  AGENT_EXECUTION_CANCELLED: "Agent执行已取消",
  AGENT_STREAM_BACKPRESSURE: "Agent事件积压过多，请重新读取会话",
  AGENT_PROVIDER_REQUEST_REJECTED: "AI服务拒绝了请求",
  AGENT_PROVIDER_UNAVAILABLE: "AI服务暂时不可用，请稍后重试",
  AGENT_TOOL_RESULT_TOO_LARGE: "Agent工具结果超出限制",
  AGENT_REPORT_CATEGORY_STALE: "风险分类已变更，请重新发起",
  AGENT_STREAM_IDLE_TIMEOUT: "Agent事件流空闲超时，请重新读取会话",
  AGENT_EXECUTION_CONFIG_INVALID: "Agent执行配置无效，请重新发起对话",
};

export function agentErrorLabel(code: string, fallback: string): string {
  return AGENT_ERROR_LABELS[code] ?? fallback;
}
