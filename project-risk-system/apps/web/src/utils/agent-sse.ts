/**
 * Pure Agent SSE parsing and event reduction (ADRs 0019 / 0028 / 0029).
 *
 * The FastAPI event stream is typed as `text/event-stream: unknown` in the
 * frozen OpenAPI authority (T032), so this module narrows the wire payload at
 * runtime with typed guards instead of substituting a hand-written contract.
 * No `any` is used: every payload field is validated before it is trusted.
 */

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

// ---------------------------------------------------------------------------
// Preview content (the canonical confirmation payload, ADR 0029)
// ---------------------------------------------------------------------------

export type AgentOperation = "REPORT" | "PROCESS" | "RESOLVE";

export interface PreviewContent {
  operation: string;
  projectId: string;
  riskId: string | null;
  todoId: string | null;
  title: string;
  description: string;
  riskLevel: string | null;
  dueDate: string | null;
  assigneeUserId: string | null;
  categoryId: string | null;
}

function parsePreviewContent(value: unknown): PreviewContent {
  const record = asRecord(value) ?? {};
  return {
    operation: asString(record.operation),
    projectId: asString(record.projectId),
    riskId: nullableString(record.riskId),
    todoId: nullableString(record.todoId),
    title: asString(record.title),
    description: asString(record.description),
    riskLevel: nullableString(record.riskLevel),
    dueDate: nullableString(record.dueDate),
    assigneeUserId: nullableString(record.assigneeUserId),
    categoryId: nullableString(record.categoryId),
  };
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
}

export interface AgentProgressState {
  stage: string;
  message: string;
}

export interface AgentPreviewState {
  token: string;
  operation: AgentOperation;
  contentDigest: string;
  expiresAt: string;
  occurredAt: string;
  content: PreviewContent;
  status: "pending" | "confirming" | "confirmed" | "failed";
  result: AgentConfirmationFeedback | null;
  /** Mapped user-facing message when confirmation fails (replay/expired/...). */
  failureMessage: string | null;
}

export interface AgentConfirmationFeedback {
  operation: string;
  resourceType: string;
  resourceId: string;
  completedAt: string;
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
  preview: AgentPreviewState | null;
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
    preview: null,
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
    case "preview": {
      next.preview = {
        token: asString(base.raw.confirmationToken),
        operation: parseOperation(base.raw.operation),
        contentDigest: asString(base.raw.contentDigest),
        expiresAt: asString(base.raw.expiresAt),
        occurredAt: base.occurredAt,
        content: parsePreviewContent(base.raw.content),
        status: "pending",
        result: null,
        failureMessage: null,
      };
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

function parseOperation(value: unknown): AgentOperation {
  return value === "REPORT" || value === "PROCESS" || value === "RESOLVE"
    ? value
    : "REPORT";
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

const CONFIRMATION_ERROR_LABELS: Record<string, string> = {
  AGENT_CONFIRMATION_ALREADY_USED: "确认凭证已被使用，请勿重复确认",
  AGENT_CONFIRMATION_EXPIRED: "确认凭证已过期，请重新发起",
  AGENT_CONFIRMATION_CONTENT_MISMATCH: "确认内容或当前授权已变化，请重新发起",
  AGENT_CONFIRMATION_IN_PROGRESS: "确认正在处理中，请稍候",
  AGENT_CONFIRMATION_OWNER_MISMATCH: "确认凭证不属于当前用户",
  AGENT_RISK_ALREADY_RESOLVED: "风险已经解除",
};

export function confirmationErrorLabel(code: string, fallback: string): string {
  return CONFIRMATION_ERROR_LABELS[code] ?? fallback;
}

export function operationLabel(operation: AgentOperation): string {
  return (
    { REPORT: "上报风险", PROCESS: "处理待办", RESOLVE: "解除风险" } as const
  )[operation];
}

/** A one-line, read-only summary of a preview's canonical content. */
export function previewSummary(content: PreviewContent): string {
  if (content.operation === "REPORT") {
    return content.title || content.description || "新建风险上报";
  }
  return content.description || "风险处理操作";
}
