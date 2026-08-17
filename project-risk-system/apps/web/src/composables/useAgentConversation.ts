import { onScopeDispose, reactive, ref } from "vue";

import {
  agentApi,
  type AgentInteractionRequest,
  type AgentMessageResponse,
} from "@/api/agent";
import { ApiError } from "@/api/http";
import {
  applyFrame,
  initialAgentState,
  parseSseFrames,
  type AgentConversationState,
  type AgentStreamMessage,
} from "@/utils/agent-sse";

/**
 * Agent conversation controller (ADRs 0019 / 0028 / 0029).
 *
 * Wires the generated REST surface (`agentApi`) and the PostgreSQL-backed SSE
 * stream to the pure reducer in `utils/agent-sse`. Owns the resilient-state
 * lifecycle: create/continue, resume-after-disconnect, retry after a provider
 * failure and durable interaction confirmation. No write occurs on
 * the stream — it is consumed read-only and reduced into view state.
 */
export function useAgentConversation() {
  const state = reactive<AgentConversationState>(initialAgentState());
  /** True while a turn is being created or streamed; disables the input. */
  const sending = ref(false);
  const lastUserMessage = ref("");
  /** Stream URL of the in-flight turn, reused for resume-after-disconnect. */
  let activeStreamUrl: string | null = null;
  let controller: AbortController | null = null;

  onScopeDispose(() => {
    abortStream();
  });

  function toStreamMessage(message: AgentMessageResponse): AgentStreamMessage {
    return {
      id: message.id,
      role: message.role === "ASSISTANT" ? "ASSISTANT" : "USER",
      content: message.content,
      createdAt: message.createdAt,
      dataAsOf: message.dataAsOf,
      sequence: message.sequence,
      structured: message.structured,
    };
  }

  /** Send a turn, creating the conversation on first use. */
  async function send(rawMessage: string): Promise<void> {
    const message = rawMessage.trim();
    if (!message || sending.value) return;
    lastUserMessage.value = message;
    sending.value = true;
    state.error = null;
    state.streamingText = "";
    state.progress = null;
    state.status = "loading";

    try {
      let streamUrl: string;
      let userMessage: AgentMessageResponse;
      if (state.conversationId) {
        const envelope = await agentApi.continueConversation(
          state.conversationId,
          message,
        );
        streamUrl = envelope.streamUrl;
        userMessage = envelope.userMessage;
      } else {
        const envelope = await agentApi.create(message);
        state.conversationId = envelope.conversation.id;
        streamUrl = envelope.streamUrl;
        userMessage = envelope.userMessage;
      }
      state.messages = [...state.messages, toStreamMessage(userMessage)];
      activeStreamUrl = streamUrl;
      state.status = "streaming";
      await connectStream(streamUrl, null);
    } catch (error) {
      applyRequestError(error);
    } finally {
      sending.value = false;
    }
  }

  /** Resume the in-flight stream from the last applied event (`after` cursor). */
  async function reconnect(): Promise<void> {
    if (!activeStreamUrl || sending.value) return;
    sending.value = true;
    state.error = null;
    state.status = "streaming";
    try {
      await connectStream(activeStreamUrl, state.lastEventId);
    } catch (error) {
      applyRequestError(error);
    } finally {
      sending.value = false;
    }
  }

  /** Re-send the last user turn after a retryable provider failure. */
  function retry(): void {
    const message = lastUserMessage.value;
    if (!message) return;
    void send(message);
  }

  /** Respond to a durable PROJECT_SELECTION or WRITE_CONFIRMATION interaction. */
  async function respondInteraction(body: AgentInteractionRequest): Promise<void> {
    const interaction = state.interaction;
    if (!interaction || interaction.status !== "OPEN" || sending.value) return;
    sending.value = true;
    state.error = null;
    try {
      const result = await agentApi.respondInteraction(interaction.id, body);
      state.interaction = result.interaction;
      if (result.streamUrl) {
        activeStreamUrl = result.streamUrl;
        state.status = "streaming";
        await connectStream(result.streamUrl, state.lastEventId);
      }
    } catch (error) {
      applyRequestError(error);
    } finally {
      sending.value = false;
    }
  }

  async function cancelInteraction(): Promise<void> {
    await respondInteraction({ action: "CANCEL" });
  }

  /** Drop all conversation state and abort any active stream. */
  function reset(): void {
    abortStream();
    activeStreamUrl = null;
    lastUserMessage.value = "";
    Object.assign(state, initialAgentState());
  }

  async function connectStream(
    streamUrl: string,
    after: string | null,
  ): Promise<void> {
    abortStream();
    controller = new AbortController();
    const url = withResumeCursor(streamUrl, after);
    let response: Response;
    try {
      response = await fetch(url, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
    } catch (error) {
      if (isAbort(error)) return;
      markDisconnected();
      return;
    }

    if (!response.ok) {
      await consumeStreamError(response);
      return;
    }

    const body = response.body;
    if (!body) {
      markDisconnected();
      return;
    }

    const reader = body.getReader();
    const decoder = new TextDecoder();
    let carry = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        carry += decoder.decode(value, { stream: true });
        const parsed = parseSseFrames(carry);
        carry = parsed.carry;
        for (const frame of parsed.frames) {
          Object.assign(state, applyFrame(state, frame));
        }
      }
      // Stream closed. If no terminal event was seen, offer to resume.
      if (state.status === "streaming") {
        markDisconnected();
      }
    } catch (error) {
      if (isAbort(error)) return;
      markDisconnected();
    }
  }

  async function consumeStreamError(response: Response): Promise<void> {
    const payload = (await response.json().catch(() => null)) as
      | { code?: string; message?: string | string[] }
      | null;
    const code = payload?.code ?? "AGENT_STREAM_UNAVAILABLE";
    const rawMessage = payload?.message;
    const message = Array.isArray(rawMessage)
      ? rawMessage.join("；")
      : rawMessage ?? "Agent事件流不可用";
    state.status = "error";
    state.error = { code, message, retryable: code !== "AGENT_EVENT_CURSOR_UNRECOVERABLE" };
  }

  function markDisconnected(): void {
    if (state.status === "completed" || state.status === "error") return;
    state.status = "disconnected";
  }

  function applyRequestError(error: unknown): void {
    if (isAbort(error)) return;
    if (error instanceof ApiError) {
      state.status = "error";
      state.error = {
        code: error.code,
        message: error.message,
        retryable: error.status >= 500 || error.status === 0,
      };
      return;
    }
    state.status = "error";
    state.error = {
      code: "AGENT_REQUEST_FAILED",
      message: error instanceof Error ? error.message : "Agent请求失败",
      retryable: true,
    };
  }

  function abortStream(): void {
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  return {
    state,
    sending,
    send,
    reconnect,
    retry,
    respondInteraction,
    cancelInteraction,
    reset,
  };
}

function withResumeCursor(streamUrl: string, after: string | null): string {
  if (!after) return streamUrl;
  const separator = streamUrl.includes("?") ? "&" : "?";
  return `${streamUrl}${separator}after=${encodeURIComponent(after)}`;
}

function isAbort(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    (error.name === "AbortError" || error.name === "TimeoutError")
  );
}
