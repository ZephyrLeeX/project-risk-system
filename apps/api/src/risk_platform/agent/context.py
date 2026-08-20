"""Bounded, provider-neutral conversation memory for Agent executions."""

# ruff: noqa: RUF002

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.v2_adapter import (
    ByteTokenEstimator,
    ProviderCandidate,
    ProviderError,
    ProviderRole,
    TokenEstimator,
)
from risk_platform.auth.service import SessionIdentity
from risk_platform.projects.query_service import ProjectsQueryService
from risk_platform.shared.errors import ApiError

from .models import AgentConversation, AgentMessage, AgentMessageRole

if TYPE_CHECKING:
    # ``ContextBudget`` lives in ``agent.core``; importing it eagerly would
    # form a cycle (``core`` imports ``context`` for the memory types), so it
    # is only referenced as an annotation here.  ``from_budget`` reads the
    # budget's attributes at runtime and never the class itself.
    from .core import ContextBudget

logger = logging.getLogger(__name__)

Summarizer = Callable[
    [tuple[ProviderCandidate, ...], str | None, str], Awaitable[str]
]


@dataclass(frozen=True, slots=True)
class ConversationContextPolicy:
    """Token-based conversation memory thresholds.

    All thresholds are *tokens*, derived from the execution's effective model
    capability via ``from_budget`` rather than fixed byte constants.  The
    service drives compression by reaching ``compression_trigger`` (a ratio
    of the history budget) and compresses toward ``compression_target`` (a
    smaller ratio); ``summary_input_budget`` bounds one summarizer transcript.
    """

    history_budget: int
    compression_trigger: int
    compression_target: int
    summary_input_budget: int = 40 * 1024
    minimum_recent_turns: int = 2
    # Safety net only.  The compression loop is driven by reaching the trigger
    # or exhausting the compressible batch; this cap exists solely to log
    # explicit degradation if a pathological conversation cannot be compressed
    # in bounded passes, never as a silent fixed stop.
    max_compression_passes: int = 32

    def __post_init__(self) -> None:
        if (
            self.history_budget <= 0
            or self.compression_target <= 0
            or self.compression_target >= self.compression_trigger
            or self.compression_trigger > self.history_budget
            or self.summary_input_budget <= 0
            or self.minimum_recent_turns < 2
            or self.max_compression_passes <= 0
        ):
            raise ValueError("invalid conversation context policy")

    @classmethod
    def from_budget(cls, budget: ContextBudget) -> ConversationContextPolicy:
        """Derive token thresholds from the execution's frozen context budget.

        Compression triggers near the ceiling (the history is about to stop
        fitting) and compresses toward half the history budget, so the loop
        is driven by the real remaining space rather than a fixed trigger
        unrelated to the model.  ``summary_input_budget`` bounds one summarizer
        transcript to half the history budget as well.
        """

        history = budget.history_budget
        return cls(
            history_budget=history,
            compression_trigger=max(1, int(history * 0.85)),
            compression_target=max(1, int(history * 0.5)),
            summary_input_budget=max(1, int(history * 0.5)),
        )


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    sequence: int
    role: ProviderRole
    content: str


@dataclass(frozen=True, slots=True)
class ActiveProject:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class AgentConversationContext:
    summary: str | None
    recent_messages: tuple[ConversationMessage, ...]
    active_project: ActiveProject | None
    summarized_through_sequence: int
    # ``True`` only when the service could not compress far enough (summarizer
    # provider failure, pass exhaustion or an empty summary) to fit every
    # unsummarized turn into the effective history budget.  A degraded context
    # is explicitly *not* a complete memory: Core must refuse to run on it
    # rather than answer as if the dropped middle turns never existed.  This
    # flag is the only thing that distinguishes "we dropped history" from "the
    # summary covers it", so it is never set silently.
    context_degraded: bool = False


@dataclass(frozen=True, slots=True)
class _Turn:
    user: AgentMessage
    assistant: AgentMessage

    @property
    def last_sequence(self) -> int:
        return self.assistant.sequence

    def transcript(self) -> str:
        return f"USER: {self.user.content}\nASSISTANT: {self.assistant.content}"


def _turn_size(turn: _Turn, estimator: TokenEstimator) -> int:
    """Estimated tokens for one user/assistant turn, including framing overhead.

    Sized in tokens by the execution's frozen estimator (conservative:
    never under-counts), so memory compression reflects the real model context
    budget rather than serialized bytes.
    """

    return (
        estimator.estimate(turn.user.content)
        + estimator.estimate(turn.assistant.content)
        + 64
    )


class ConversationContextService:
    """Build memory from PostgreSQL without treating memory as live business data."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        summarizer: Summarizer,
        policy: ConversationContextPolicy,
        *,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self._sessions = sessions
        self._summarizer = summarizer
        self._policy = policy
        # Conservative default (one estimated token per UTF-8 byte) keeps the
        # service usable without a candidate snapshot; a real execution passes
        # the provider-specific estimator so memory is sized in real model
        # tokens rather than bytes.
        self._estimator = estimator or ByteTokenEstimator()
        self._projects = ProjectsQueryService(sessions)

    async def build(
        self,
        conversation_id: UUID,
        current_user_message_id: UUID,
        identity: SessionIdentity,
        snapshot: tuple[ProviderCandidate, ...],
        *,
        history_budget: int | None = None,
        policy: ConversationContextPolicy | None = None,
        estimator: TokenEstimator | None = None,
    ) -> AgentConversationContext:
        # The history budget may be supplied per-execution from the real fixed
        # overhead (actual system instruction + tool definitions + current
        # message + reserves).  When absent, fall back to the static policy.
        # The per-call ``policy`` / ``estimator`` overrides let one execution
        # freeze the capability-derived thresholds and the provider-specific
        # estimator once and thread them through memory building, so the same
        # immutable snapshot sizes both the loop budget and the conversation
        # context.
        active_policy = policy or self._policy
        active_estimator = estimator or self._estimator
        effective_budget = (
            history_budget if history_budget is not None and history_budget > 0
            else active_policy.history_budget
        )
        passes = 0
        # The compression threshold must be bounded by the *effective* history
        # budget, not only the static policy trigger.  When the per-execution
        # fixed overhead shrinks the real history budget below the configured
        # trigger, compressing only at the trigger would let unsummarized
        # history exceed the budget and be silently truncated by ``_result``.
        # Using ``min(trigger, effective_budget)`` forces compression before any
        # turn would have to be dropped.
        trigger = min(active_policy.compression_trigger, effective_budget)
        while True:
            conversation, _, turns = await self._load(
                conversation_id, current_user_message_id, UUID(identity.user.id)
            )
            active = await self._active_project(conversation, identity)
            if self._context_size(conversation.contextSummary, turns, active_estimator) <= trigger:
                return self._result(
                    conversation, turns, active, effective_budget, active_estimator, active_policy
                )
            compressible = turns[: -active_policy.minimum_recent_turns]
            batch = self._bounded_prefix(compressible, active_estimator, active_policy)
            # No compressible batch means only the protected recent window
            # remains unsummarized; everything older is already in the summary,
            # so the no-dropped-history invariant holds by construction.
            if not batch:
                return self._result(
                    conversation, turns, active, effective_budget, active_estimator, active_policy
                )
            if passes >= active_policy.max_compression_passes:
                # Execution-budget exhaustion is explicit degradation, never a
                # silent stop.  We do not claim the unsummarized middle turns
                # have entered memory; the persisted summary + latest turns are
                # returned and the next turn resumes monotonic compression.
                logger.warning("AGENT_CONTEXT_COMPRESSION_DEGRADED")
                return self._result(
                    conversation, turns, active, effective_budget, active_estimator, active_policy
                )
            passes += 1
            through = batch[-1].last_sequence
            transcript = "\n\n".join(turn.transcript() for turn in batch)
            try:
                summary = await self._summarizer(
                    snapshot, conversation.contextSummary, transcript
                )
            except ProviderError:
                logger.warning("AGENT_CONTEXT_COMPRESSION_DEGRADED")
                return self._result(
                    conversation, turns, active, effective_budget, active_estimator, active_policy
                )
            summary = self._bounded_text(
                summary, active_policy.compression_target, active_estimator
            )
            if not summary:
                logger.warning("AGENT_CONTEXT_COMPRESSION_DEGRADED")
                return self._result(
                    conversation, turns, active, effective_budget, active_estimator, active_policy
                )
            updated = await self._compare_and_set_summary(
                conversation.id,
                expected_version=conversation.contextSummaryVersion,
                expected_through=conversation.contextSummaryThroughSequence,
                new_summary=summary,
                new_through=through,
            )
            if not updated:
                continue
        conversation, _, turns = await self._load(
            conversation_id, current_user_message_id, UUID(identity.user.id)
        )
        return self._result(
            conversation,
            turns,
            await self._active_project(conversation, identity),
            effective_budget,
            active_estimator,
            active_policy,
        )

    async def _load(
        self, conversation_id: UUID, current_message_id: UUID, owner_id: UUID
    ) -> tuple[AgentConversation, AgentMessage, list[_Turn]]:
        async with self._sessions() as session:
            conversation = await session.scalar(
                select(AgentConversation).where(
                    AgentConversation.id == conversation_id,
                    AgentConversation.ownerUserId == owner_id,
                )
            )
            current = await session.get(AgentMessage, current_message_id)
            if (
                conversation is None
                or current is None
                or current.conversationId != conversation_id
                or current.role is not AgentMessageRole.USER
            ):
                raise RuntimeError("AGENT_EXECUTION_CONFIG_INVALID")
            messages = list(
                (
                    await session.scalars(
                        select(AgentMessage)
                        .where(
                            AgentMessage.conversationId == conversation_id,
                            AgentMessage.sequence
                            > conversation.contextSummaryThroughSequence,
                            AgentMessage.sequence < current.sequence,
                            AgentMessage.role.in_(
                                (AgentMessageRole.USER, AgentMessageRole.ASSISTANT)
                            ),
                        )
                        .order_by(AgentMessage.sequence)
                    )
                ).all()
            )
        return conversation, current, self._completed_turns(messages)

    async def _active_project(
        self, conversation: AgentConversation, identity: SessionIdentity
    ) -> ActiveProject | None:
        if conversation.activeProjectId is None:
            return None
        try:
            project = await self._projects.detail(identity, conversation.activeProjectId)
        except ApiError:
            async with self._sessions.begin() as session:
                current = await session.scalar(
                    select(AgentConversation)
                    .where(AgentConversation.id == conversation.id)
                    .with_for_update()
                )
                if current is not None and current.activeProjectId == conversation.activeProjectId:
                    current.activeProjectId = None
                    current.activeProjectName = None
                    current.contextUpdatedAt = datetime.now(UTC)
            return None
        return ActiveProject(project.id, project.name)

    async def _compare_and_set_summary(
        self,
        conversation_id: UUID,
        *,
        expected_version: int,
        expected_through: int,
        new_summary: str,
        new_through: int,
    ) -> bool:
        async with self._sessions.begin() as session:
            conversation = await session.scalar(
                select(AgentConversation)
                .where(AgentConversation.id == conversation_id)
                .with_for_update()
            )
            if (
                conversation is None
                or conversation.contextSummaryVersion != expected_version
                or conversation.contextSummaryThroughSequence != expected_through
                or new_through <= expected_through
            ):
                return False
            conversation.contextSummary = new_summary
            conversation.contextSummaryThroughSequence = new_through
            conversation.contextSummaryVersion += 1
            conversation.contextUpdatedAt = datetime.now(UTC)
            return True

    def _result(
        self,
        conversation: AgentConversation,
        turns: Sequence[_Turn],
        active: ActiveProject | None,
        history_budget: int,
        estimator: TokenEstimator,
        active_policy: ConversationContextPolicy,
    ) -> AgentConversationContext:
        selected: list[_Turn] = []
        used = estimator.estimate(conversation.contextSummary or "")
        for turn in reversed(turns):
            required = len(selected) < active_policy.minimum_recent_turns
            if not required and used + _turn_size(turn, estimator) > history_budget:
                break
            selected.append(turn)
            used += _turn_size(turn, estimator)
        selected.reverse()
        # No-dropped-history invariant: every eligible turn (sequence >
        # summarized_through_sequence) must either be carried in
        # recent_messages or covered by the summary boundary.  ``turns`` holds
        # exactly the eligible turns; if any did not fit the budget it is
        # absent from ``selected`` and would be silently lost.  Surface that as
        # an explicit degraded state instead of returning an incomplete memory
        # that looks complete.
        degraded = len(selected) < len(turns)
        messages: list[ConversationMessage] = []
        for turn in selected:
            messages.extend(
                (
                    ConversationMessage(
                        turn.user.sequence, ProviderRole.USER, turn.user.content
                    ),
                    ConversationMessage(
                        turn.assistant.sequence,
                        ProviderRole.ASSISTANT,
                        turn.assistant.content,
                    ),
                )
            )
        return AgentConversationContext(
            summary=conversation.contextSummary,
            recent_messages=tuple(messages),
            active_project=active,
            summarized_through_sequence=conversation.contextSummaryThroughSequence,
            context_degraded=degraded,
        )

    def _bounded_prefix(
        self,
        turns: Sequence[_Turn],
        estimator: TokenEstimator,
        active_policy: ConversationContextPolicy,
    ) -> list[_Turn]:
        result: list[_Turn] = []
        used = 0
        for turn in turns:
            if used + _turn_size(turn, estimator) > active_policy.summary_input_budget:
                break
            result.append(turn)
            used += _turn_size(turn, estimator)
        return result

    @staticmethod
    def _completed_turns(messages: Sequence[AgentMessage]) -> list[_Turn]:
        result: list[_Turn] = []
        pending: AgentMessage | None = None
        for message in messages:
            if message.role is AgentMessageRole.USER:
                pending = message
            elif message.role is AgentMessageRole.ASSISTANT and pending is not None:
                result.append(_Turn(pending, message))
                pending = None
        return result

    @staticmethod
    def _context_size(
        summary: str | None, turns: Sequence[_Turn], estimator: TokenEstimator
    ) -> int:
        return estimator.estimate(summary or "") + sum(
            _turn_size(turn, estimator) for turn in turns
        )

    @staticmethod
    def _bounded_text(value: str, budget: int, estimator: TokenEstimator) -> str:
        # ``budget`` is tokens, not UTF-8 bytes: truncating by raw bytes would
        # under-size CJK-heavy summaries (3 B/token) relative to the token
        # budget and could split a multibyte sequence.  Truncate by the
        # estimator's token count instead.  ``estimate`` is monotonic
        # non-decreasing in the UTF-8 byte length for the byte-derived
        # estimators in use, so binary-search the largest byte prefix whose
        # re-decoded estimate stays within the token budget.  Decoding with
        # errors ignored drops any partial trailing multibyte sequence, so the
        # decoded prefix is always valid and its estimate never exceeds the
        # byte-prefix's (it holds a subset of the same bytes).
        text = value.strip()
        if not text or estimator.estimate(text) <= budget:
            return text
        encoded = text.encode("utf-8")
        lo, hi, best = 0, len(encoded), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = encoded[:mid].decode("utf-8", errors="ignore")
            if estimator.estimate(candidate) <= budget:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return encoded[:best].decode("utf-8", errors="ignore").rstrip()


def refers_to_active_project(message: str) -> bool:
    normalized = "".join(message.split())
    return any(
        marker in normalized
        for marker in ("这个项目", "该项目", "刚才的项目", "刚才那个项目", "上面的项目")
    )


# Closed, domain-anchored vocabulary.  This is a *positive* allowlist of the
# system's own risk-management concepts and follow-up actions — NOT a chase-the-
# bad-verb blacklist.  Inheritance is granted only when a shorthand positively
# resolves to one of these; anything else fails closed.
_REFERENCE_MARKERS = (
    "这个",
    "那个",
    "第一个",
    "第二个",
    "第三个",
    "刚才",
    "上面",
    "上述",
    "继续",
    "接着",
    "上一个问题",
    "刚才的问题",
    "前一个问题",
)
_CORRECTION_MARKERS = ("不是", "我说的是")
_DOMAIN_TERMS = ("项目", "风险", "待办", "周报", "看板", "状态", "数量", "金额")


def _is_referential_shorthand(normalized: str) -> bool:
    return any(marker in normalized for marker in _REFERENCE_MARKERS) or any(
        marker in normalized for marker in _CORRECTION_MARKERS
    )


def _has_domain_anchor(context: AgentConversationContext) -> bool:
    """A server-owned or recently-established domain context to inherit from."""

    if context.active_project is not None:
        return True
    return any(
        any(term in (item.content or "") for term in _DOMAIN_TERMS)
        for item in context.recent_messages
    )


def is_contextual_shorthand(
    message: str, context: AgentConversationContext
) -> bool:
    """Whether an out-of-scope message is an anchored short reference/correction.

    Three-state scope (see ``ScopeDecision``): layer 1 (``ScopePolicy.decide``)
    hard-rejects a request only when it is *clearly* out of scope — a fresh
    general request with no domain keyword and no established domain context
    to inherit.  A short referential shorthand or correction ("不是 A，我说的是
    B", "第二个", "这个…", "继续上一个问题") that is anchored to a prior domain turn
    (the server-owned active project, or a recent turn carrying a domain term) is
    *contextually ambiguous*: deterministic text cannot tell a bare project
    name ("江湾") from a non-domain topic ("天气") without an ever-expanding
    blacklist, so it is deferred to the model-level ``AGENT_SCOPE_POLICY``
    (layer 2) instead of being hard-admitted or hard-rejected here.  Layer 2
    re-refuses anything genuinely out of scope ("不是，我说的是天气") even after
    this gate lets it through.

    A shorthand with no domain anchor to inherit from still fails closed —
    "第二个展开说一下" with no prior domain turn has nothing to resolve to.
    """

    normalized = "".join(message.split())
    if not normalized or len(normalized) > 120:
        return False
    if not _is_referential_shorthand(normalized):
        return False
    return _has_domain_anchor(context)


__all__ = [
    "ActiveProject",
    "AgentConversationContext",
    "ConversationContextPolicy",
    "ConversationContextService",
    "ConversationMessage",
    "is_contextual_shorthand",
    "refers_to_active_project",
]
