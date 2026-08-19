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
  withResumeCursor,
  type AgentConversationState,
  type AgentStreamCursor,
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
/**
 * Slow-phase cadence used after the fast {@link TERMINAL_POLL_DEADLINE_MS} has
 * elapsed while the worker is still RUNNING.  Bounded only by the worker's own
 * 90 s timeout/lease; an unmount stops it via the drain-generation token so a
 * stuck worker cannot pin the input forever.
 */
const SLOW_POLL_INTERVAL_MS = 2000;

export function useAgentConversation() {
  const state = reactive<AgentConversationState>(initialAgentState());
  /** True while a turn is being created or streamed; disables the input. */
  const sending = ref(false);
  const lastUserMessage = ref("");
  /** Stream URL of the in-flight turn, reused for resume-after-disconnect. */
  let activeStreamUrl: string | null = null;
  let controller: AbortController | null = null;
  let lastInteractionBody: AgentInteractionRequest | null = null;
  /**
   * Generation token for the slow-phase cancel drain.  Bumped by
   * {@link dispose} / {@link reset} so an unmount or a new conversation stops
   * a still-running poll loop instead of reconciling against a conversation the
   * user has left.
   */
  let drainGeneration = 0;

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
      let baselineSequence: number;
      if (state.conversationId) {
        const envelope = await agentApi.continueConversation(
          state.conversationId,
          message,
        );
        streamUrl = envelope.streamUrl;
        userMessage = envelope.userMessage;
        baselineSequence = envelope.resumeAfterEventSequence;
      } else {
        const envelope = await agentApi.create(message);
        state.conversationId = envelope.conversation.id;
        persistConversationId(envelope.conversation.id);
        streamUrl = envelope.streamUrl;
        userMessage = envelope.userMessage;
        baselineSequence = envelope.resumeAfterEventSequence;
      }
      state.messages = [...state.messages, toStreamMessage(userMessage)];
      activeStreamUrl = streamUrl;
      state.status = "streaming";
      // Open the stream from the sequence baseline the POST response carried
      // (create/continue), NOT null. The baseline is conversation
      // .lastEventSequence snapshotted in the task-creating transaction before
      // the worker could see the task; resuming from it replays the terminal
      // events the worker writes in the POST→SSE gap instead of losing them
      // (after=null re-reads the tail at GET time and opens past those events).
      await connectStream(streamUrl, { kind: "sequence", value: baselineSequence });
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
      await connectStream(activeStreamUrl, eventIdCursor(state.lastEventId));
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
        // Resume the new execution from the sequence baseline the respond
        // response carried (conversation.lastEventSequence at respond time),
        // not the event-id cursor: the resumed execution's events are written
        // in the POST→SSE gap and must replay instead of being lost.
        await connectStream(result.streamUrl, {
          kind: "sequence",
          value: result.resumeAfterEventSequence ?? 0,
        });
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
    drainGeneration += 1;
  }

  /** Drop all conversation state, clear the persisted reference, and start over. */
  function reset(): void {
    abortStream();
    activeStreamUrl = null;
    lastInteractionBody = null;
    drainGeneration += 1;
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
        if (runtime.cancellationRequested) {
          // A refresh during an explicit cancel: the worker is still RUNNING
          // but the cancel flag is set, so do NOT reopen the normal stream (it
          // would race the draining turn) and do NOT re-enable the input. Stay
          // "cancelling" and poll the runtime on the slow cadence until the
          // worker reaches a terminal status, then reconcile.  (ADR 0036: the
          // closed AgentExecution.status enum has no CANCELLING value, so
          // cancellationRequested is the restore signal.)
          activeStreamUrl = null;
          state.status = "cancelling";
          void drainCancellation(history.conversation.id);
        } else {
          // A refresh mid-turn reattaches to the SAME durable execution instead
          // of forcing a re-send.  The stream resumes from the snapshot
          // sequence cursor (resumeAfterEventSequence → ?afterSequence=<n>),
          // NOT null and NOT the event-id cursor: when the worker writes the
          // terminal MESSAGE_DELTA/COMPLETED events in the gap between this
          // history response and the SSE GET, after=null re-reads the
          // conversation tail at request time and the stream opens *after*
          // those events, observes a terminal task, and closes with no event —
          // the UI goes disconnected and the assistant answer is lost. The
          // sequence cursor is always defined (a brand-new turn is 0, where the
          // event-id cursor is null), so it is the only cursor that also closes
          // the zero-event race.
          activeStreamUrl = runtime.streamUrl;
          state.status = "streaming";
          await connectStream(runtime.streamUrl, {
            kind: "sequence",
            value: runtime.resumeAfterEventSequence ?? 0,
          });
        }
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
      // Fast path: the worker reached a terminal status within the 15 s
      // UI-poll deadline — reconcile any final assistant message (a cancel
      // that lost the race against normal completion) and release the input.
      reconcileTerminal(terminalHistory);
      return;
    }
    // Slow path: the 15 s deadline elapsed while the worker is still RUNNING.
    // Do NOT flip to completed — that would re-enable the input and the next
    // send would hit a misleading 409 AGENT_EXECUTION_ACTIVE.  Keep the status
    // "cancelling" (input disabled, label "取消中", "已提交停止请求，后台任务
    // 仍在结束中…") and drain the runtime on the slow cadence until the worker
    // self-terminates, then reconcile.  An unmount stops the loop.
    void drainCancellation(state.conversationId);
  }

  /** Reconcile visible messages from a terminal history and release the input. */
  function reconcileTerminal(history: AgentConversationHistory): void {
    state.messages = history.messages
      .filter((message) => message.role === "USER" || message.role === "ASSISTANT")
      .map(toStreamMessage);
    state.streamingText = "";
    state.progress = null;
    state.status = "completed";
  }

  /**
   * Slow-phase drain after the fast 15 s cancel deadline elapsed while the
   * worker is still RUNNING.  Keep the input locked ("取消中") and poll
   * history.runtime on the slow cadence until the execution is no longer
   * RUNNING, then reconcile to the terminal messages.  Stopped by
   * {@link dispose} / {@link reset} via the generation token so an unmount
   * cannot leave a rogue poll loop running against a conversation the user
   * has left.
   */
  async function drainCancellation(conversationId: string): Promise<void> {
    const generation = drainGeneration;
    while (generation === drainGeneration) {
      await delay(SLOW_POLL_INTERVAL_MS);
      if (generation !== drainGeneration) return;
      let history: AgentConversationHistory;
      try {
        history = await agentApi.history(conversationId);
      } catch {
        // history is best-effort during the slow drain; keep polling until the
        // worker reaches a terminal status or the user unmounts.
        continue;
      }
      const runtime = history.runtime;
      // Only a terminal runtime (null — WAITING_FOR_USER and RUNNING are both
      // active) releases the cancelling state. WAITING_FOR_USER must NOT be
      // treated as terminal: a cancel that raced a PROJECT_SELECTION /
      // WRITE_CONFIRMATION transition would otherwise surface a stale OPEN
      // interaction and re-enable the input for a turn the user stopped. The
      // backend post-core cancellation fence prevents that race; this keeps
      // the input locked if it ever leaks through.
      if (!runtime) {
        reconcileTerminal(history);
        return;
      }
    }
  }

  async function connectStream(
    streamUrl: string,
    cursor: AgentStreamCursor,
  ): Promise<void> {
    let resumeCursor = cursor;
    const reconnectDelays = [1000, 2000, 5000];
    for (let attempt = 0; attempt <= reconnectDelays.length; attempt += 1) {
      // Stop reconnecting the moment the turn is no longer streaming (an
      // explicit cancel set "cancelling" or a terminal event landed) so a
      // pending reconnect delay cannot race a cancel and open a rogue fetch.
      if (state.status !== "streaming") return;
      const endedUnexpectedly = await connectStreamOnce(streamUrl, resumeCursor);
      if (!endedUnexpectedly || state.status !== "streaming") return;
      if (attempt === reconnectDelays.length) {
        markDisconnected();
        return;
      }
      await delay(reconnectDelays[attempt]!);
      // On reconnect, resume from the last applied durable event id (the
      // transport cursor), not the snapshot sequence — the sequence cursor is
      // only for the initial restore from a history snapshot.
      resumeCursor = eventIdCursor(state.lastEventId);
    }
  }

  /** Consume one HTTP stream. A true result means EOF without a terminal event. */
  async function connectStreamOnce(
    streamUrl: string,
    cursor: AgentStreamCursor,
  ): Promise<boolean> {
    abortStream();
    controller = new AbortController();
    const url = withResumeCursor(streamUrl, cursor);
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

/**
 * Build an `eventId` resume cursor from the last applied durable event id, or
 * `null` when no event has been applied yet (the request-time tail semantic).
 * Used on transport reconnect — the snapshot sequence cursor is only for the
 * initial restore from a history snapshot.
 */
function eventIdCursor(lastEventId: string | null): AgentStreamCursor {
  return lastEventId ? { kind: "eventId", value: lastEventId } : null;
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
      // Release only on a terminal runtime (null). WAITING_FOR_USER is still
      // active (the turn has not drained to a terminal status), so keep
      // polling until the deadline; the slow drain takes over if the worker
      // is still running or has leaked a WAITING_FOR_USER race.
      if (!history.runtime) {
        return history;
      }
    } catch {
      // history is best-effort during cancel; keep polling until the deadline.
    }
    await delay(TERMINAL_POLL_INTERVAL_MS);
  }
  return null;
}
