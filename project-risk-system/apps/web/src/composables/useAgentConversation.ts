import { onScopeDispose, reactive, ref } from "vue";

import {
  agentApi,
  type AgentConversationHistory,
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

/** localStorage key for the latest conversation id reference (not chat text). */
const AGENT_CONVERSATION_KEY = "risk-platform:agent-conversation-id";

function readStoredConversationId(): string | null {
  try {
    return globalThis.localStorage.getItem(AGENT_CONVERSATION_KEY);
  } catch {
    return null;
  }
}

function persistConversationId(conversationId: string): void {
  try {
    globalThis.localStorage.setItem(AGENT_CONVERSATION_KEY, conversationId);
  } catch {
    /* storage unavailable (private mode / disabled) — restore is best-effort */
  }
}

function clearStoredConversationId(): void {
  try {
    globalThis.localStorage.removeItem(AGENT_CONVERSATION_KEY);
  } catch {
    /* ignore */
  }
}

/** Bounded deadline to wait for the worker to observe an explicit cancel. */
const TERMINAL_POLL_DEADLINE_MS = 15_000;
/** Cadence at which history.runtime is polled while an explicit cancel drains. */
const TERMINAL_POLL_INTERVAL_MS = 400;

export function useAgentConversation() {
  const state = reactive<AgentConversationState>(initialAgentState());
  /** True while a turn is being created or streamed; disables the input. */
  const sending = ref(false);
  const lastUserMessage = ref("");
  /** Stream URL of the in-flight turn, reused for resume-after-disconnect. */
  let activeStreamUrl: string | null = null;
  let controller: AbortController | null = null;
  let lastInteractionBody: AgentInteractionRequest | null = null;

  onScopeDispose(() => {
    dispose();
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
    if (!message || sending.value || state.status === "cancelling") return;
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
        persistConversationId(envelope.conversation.id);
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
    lastInteractionBody = body;
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

  function retryInteraction(): void {
    if (lastInteractionBody) void respondInteraction(lastInteractionBody);
  }

  async function cancelInteraction(): Promise<void> {
    await respondInteraction({ action: "CANCEL" });
  }

  /**
   * Tear down the live stream without dropping the conversation.
   *
   * Used on component unmount: the in-flight SSE fetch is aborted and the
   * transient stream handle is cleared (so a stale URL can never drive a
   * reconnect), but the persisted conversation-id reference and the visible
   * state are kept so a fresh mount's {@link restore} rehydrates the *same*
   * authorized conversation instead of starting over. Contrast with
   * {@link reset}, which clears the reference for a brand-new conversation.
   */
  function dispose(): void {
    abortStream();
    activeStreamUrl = null;
    lastInteractionBody = null;
  }

  /** Drop all conversation state, clear the persisted reference, and start over. */
  function reset(): void {
    abortStream();
    activeStreamUrl = null;
    lastInteractionBody = null;
    lastUserMessage.value = "";
    clearStoredConversationId();
    Object.assign(state, initialAgentState());
  }

  /**
   * Restore the most recent conversation after a page refresh.
   *
   * Only the conversation id reference is persisted locally (never the chat
   * text); the visible USER/ASSISTANT history is re-fetched from the server so
   * the next message continues the same authorized context. A missing or
   * expired conversation is cleared silently so a fresh one is created next.
   */
  async function restore(): Promise<void> {
    const conversationId = readStoredConversationId();
    if (!conversationId || sending.value || state.conversationId) return;
    sending.value = true;
    state.error = null;
    try {
      const history = await agentApi.history(conversationId);
      state.conversationId = history.conversation.id;
      state.messages = history.messages
        .filter((message) => message.role === "USER" || message.role === "ASSISTANT")
        .map(toStreamMessage);
      state.streamingText = "";
      state.progress = null;
      const runtime = history.runtime;
      if (runtime && runtime.status === "RUNNING" && runtime.streamUrl) {
        // A refresh mid-turn reattaches to the SAME durable execution instead
        // of forcing a re-send.  The stream resumes from the snapshot cursor
        // (resumeAfterEventId → ?after=<id>), NOT null: otherwise the SSE GET
        // re-reads the conversation tail at request time and, if the worker
        // wrote the terminal MESSAGE_DELTA/COMPLETED events in the gap between
        // this history response and the SSE GET, the stream opens *after*
        // them, observes a terminal task, and closes with no event — the UI
        // goes disconnected and the assistant answer is lost.
        activeStreamUrl = runtime.streamUrl;
        state.status = "streaming";
        await connectStream(
          runtime.streamUrl,
          runtime.resumeAfterEventId ?? null,
        );
      } else if (runtime && runtime.status === "WAITING_FOR_USER" && runtime.interaction) {
        // Redisplay the OPEN interaction (project selection / write
        // confirmation draft) so the user can resolve it without re-typing.
        state.interaction = runtime.interaction;
        state.status = "completed";
      } else {
        // COMPLETED / FAILED / CANCELLED / none — the turn is final and the
        // restored messages are the source of truth.
        state.status = state.messages.length ? "completed" : "idle";
      }
    } catch (error) {
      // 404 / expired / revoked: drop the stale reference so the next send
      // starts a fresh conversation instead of looping on a dead id.
      if (error instanceof ApiError && (error.status === 404 || error.status === 403)) {
        clearStoredConversationId();
      }
    } finally {
      sending.value = false;
    }
  }

  /**
   * Explicitly cancel the live execution (`POST /agent/conversations/{id}/cancel`).
   *
   * Unlike transport disconnect (refresh / tab close), this is an intentional
   * user action: the worker-polled cancel flag is set and the in-flight stream
   * is aborted locally. The turn ends without a final assistant message; the
   * already-visible USER message and history stay.
   */
  async function cancel(): Promise<void> {
    if (!state.conversationId) return;
    // Set "cancelling" synchronously BEFORE the POST so an immediate next send
    // is blocked by the send() guard while the worker has not yet reached a
    // terminal status — otherwise the still-RUNNING execution answers
    // continueConversation with a misleading 409 AGENT_EXECUTION_ACTIVE and
    // the UI reports a busy error the user did not cause.  The input stays
    // disabled (label "取消中") until the runtime is inactive.
    state.status = "cancelling";
    state.error = null;
    try {
      await agentApi.cancelConversation(state.conversationId);
    } catch (error) {
      applyRequestError(error);
      return;
    }
    abortStream();
    activeStreamUrl = null;
    state.streamingText = "";
    state.progress = null;
    // The worker observes the cancel flag asynchronously; poll history.runtime
    // until the execution is no longer RUNNING, then reconcile and release.
    const terminalHistory = await waitForRuntimeInactive(state.conversationId);
    if (terminalHistory) {
      state.messages = terminalHistory.messages
        .filter((message) => message.role === "USER" || message.role === "ASSISTANT")
        .map(toStreamMessage);
      state.streamingText = "";
      state.progress = null;
    }
    state.status = "completed";
  }

  async function connectStream(
    streamUrl: string,
    after: string | null,
  ): Promise<void> {
    let cursor = after;
    const reconnectDelays = [1000, 2000, 5000];
    for (let attempt = 0; attempt <= reconnectDelays.length; attempt += 1) {
      // Stop reconnecting the moment the turn is no longer streaming (an
      // explicit cancel set "cancelling" or a terminal event landed) so a
      // pending reconnect delay cannot race a cancel and open a rogue fetch.
      if (state.status !== "streaming") return;
      const endedUnexpectedly = await connectStreamOnce(streamUrl, cursor);
      if (!endedUnexpectedly || state.status !== "streaming") return;
      if (attempt === reconnectDelays.length) {
        markDisconnected();
        return;
      }
      await delay(reconnectDelays[attempt]!);
      cursor = state.lastEventId;
    }
  }

  /** Consume one HTTP stream. A true result means EOF without a terminal event. */
  async function connectStreamOnce(
    streamUrl: string,
    after: string | null,
  ): Promise<boolean> {
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
      if (isAbort(error)) return false;
      return true;
    }

    if (!response.ok) {
      await consumeStreamError(response);
      return false;
    }

    const body = response.body;
    if (!body) {
      return false;
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
      // Clean EOF is unexpected while the durable turn is still streaming.
      return state.status === "streaming";
    } catch (error) {
      if (isAbort(error)) return false;
      return true;
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
    if (
      state.status === "completed" ||
      state.status === "error" ||
      state.status === "cancelling"
    )
      return;
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
    retryInteraction,
    cancelInteraction,
    cancel,
    dispose,
    reset,
    restore,
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

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/**
 * Poll history.runtime until the live execution is no longer RUNNING.
 *
 * The worker sets an explicit cancel to CANCELLED only after it observes the
 * cancel flag (polled asynchronously), so the runtime can still report
 * RUNNING for a few hundred milliseconds after POST /cancel resolves.  This
 * keeps the input in the "cancelling" state across that window.  Returns the
 * terminal history so the caller can reconcile any final assistant message
 * (a cancel that lost the race against normal completion); returns null on
 * the bounded deadline so a stuck worker cannot pin the input forever — the
 * next send reconciles against the true runtime via the 409 path.
 */
async function waitForRuntimeInactive(
  conversationId: string,
): Promise<AgentConversationHistory | null> {
  const deadline = Date.now() + TERMINAL_POLL_DEADLINE_MS;
  while (Date.now() < deadline) {
    try {
      const history = await agentApi.history(conversationId);
      if (!history.runtime || history.runtime.status !== "RUNNING") {
        return history;
      }
    } catch {
      // history is best-effort during cancel; keep polling until the deadline.
    }
    await delay(TERMINAL_POLL_INTERVAL_MS);
  }
  return null;
}
