"""Bounded, provider-neutral conversation memory for Agent executions."""

# ruff: noqa: RUF002

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.v2_adapter import ProviderCandidate, ProviderError, ProviderRole
from risk_platform.auth.service import SessionIdentity
from risk_platform.projects.query_service import ProjectsQueryService
from risk_platform.shared.errors import ApiError

from .models import AgentConversation, AgentMessage, AgentMessageRole

logger = logging.getLogger(__name__)

Summarizer = Callable[
    [tuple[ProviderCandidate, ...], str | None, str], Awaitable[str]
]


@dataclass(frozen=True, slots=True)
class ConversationContextPolicy:
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

    @property
    def size(self) -> int:
        return len(self.user.content.encode()) + len(self.assistant.content.encode()) + 64

    def transcript(self) -> str:
        return f"USER: {self.user.content}\nASSISTANT: {self.assistant.content}"


class ConversationContextService:
    """Build memory from PostgreSQL without treating memory as live business data."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        summarizer: Summarizer,
        policy: ConversationContextPolicy,
    ) -> None:
        self._sessions = sessions
        self._summarizer = summarizer
        self._policy = policy
        self._projects = ProjectsQueryService(sessions)

    async def build(
        self,
        conversation_id: UUID,
        current_user_message_id: UUID,
        identity: SessionIdentity,
        snapshot: tuple[ProviderCandidate, ...],
        *,
        history_budget: int | None = None,
    ) -> AgentConversationContext:
        # The history budget may be supplied per-execution from the real fixed
        # overhead (actual system instruction + tool definitions + current
        # message + reserves).  When absent, fall back to the static policy.
        effective_budget = (
            history_budget if history_budget is not None and history_budget > 0
            else self._policy.history_budget
        )
        passes = 0
        # The compression threshold must be bounded by the *effective* history
        # budget, not only the static policy trigger.  When the per-execution
        # fixed overhead shrinks the real history budget below the configured
        # trigger, compressing only at the trigger would let unsummarized
        # history exceed the budget and be silently truncated by ``_result``.
        # Using ``min(trigger, effective_budget)`` forces compression before any
        # turn would have to be dropped.
        trigger = min(self._policy.compression_trigger, effective_budget)
        while True:
            conversation, _, turns = await self._load(
                conversation_id, current_user_message_id, UUID(identity.user.id)
            )
            active = await self._active_project(conversation, identity)
            if self._context_size(conversation.contextSummary, turns) <= trigger:
                return self._result(conversation, turns, active, effective_budget)
            compressible = turns[: -self._policy.minimum_recent_turns]
            batch = self._bounded_prefix(compressible)
            # No compressible batch means only the protected recent window
            # remains unsummarized; everything older is already in the summary,
            # so the no-dropped-history invariant holds by construction.
            if not batch:
                return self._result(conversation, turns, active, effective_budget)
            if passes >= self._policy.max_compression_passes:
                # Execution-budget exhaustion is explicit degradation, never a
                # silent stop.  We do not claim the unsummarized middle turns
                # have entered memory; the persisted summary + latest turns are
                # returned and the next turn resumes monotonic compression.
                logger.warning("AGENT_CONTEXT_COMPRESSION_DEGRADED")
                return self._result(conversation, turns, active, effective_budget)
            passes += 1
            through = batch[-1].last_sequence
            transcript = "\n\n".join(turn.transcript() for turn in batch)
            try:
                summary = await self._summarizer(
                    snapshot, conversation.contextSummary, transcript
                )
            except ProviderError:
                logger.warning("AGENT_CONTEXT_COMPRESSION_DEGRADED")
                return self._result(conversation, turns, active, effective_budget)
            summary = self._bounded_text(summary, self._policy.compression_target)
            if not summary:
                logger.warning("AGENT_CONTEXT_COMPRESSION_DEGRADED")
                return self._result(conversation, turns, active, effective_budget)
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
    ) -> AgentConversationContext:
        selected: list[_Turn] = []
        used = len((conversation.contextSummary or "").encode())
        for turn in reversed(turns):
            required = len(selected) < self._policy.minimum_recent_turns
            if not required and used + turn.size > history_budget:
                break
            selected.append(turn)
            used += turn.size
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

    def _bounded_prefix(self, turns: Sequence[_Turn]) -> list[_Turn]:
        result: list[_Turn] = []
        used = 0
        for turn in turns:
            if used + turn.size > self._policy.summary_input_budget:
                break
            result.append(turn)
            used += turn.size
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
    def _context_size(summary: str | None, turns: Sequence[_Turn]) -> int:
        return len((summary or "").encode()) + sum(turn.size for turn in turns)

    @staticmethod
    def _bounded_text(value: str, budget: int) -> str:
        encoded = value.strip().encode()
        if len(encoded) <= budget:
            return value.strip()
        return encoded[:budget].decode(errors="ignore").rstrip()


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
)
_CORRECTION_MARKERS = ("不是", "我说的是")
_DOMAIN_TERMS = ("项目", "风险", "待办", "周报", "看板", "状态", "数量", "金额")
_DOMAIN_FOLLOWUP_VERBS = (
    "展开",
    "说一下",
    "详情",
    "处理",
    "查询",
    "上报",
    "完成",
    "新增",
    "修改",
    "还有",
    "多少",
    "列表",
)


def _is_referential_shorthand(normalized: str) -> bool:
    return any(marker in normalized for marker in _REFERENCE_MARKERS) or any(
        marker in normalized for marker in _CORRECTION_MARKERS
    )


def _has_domain_query(normalized: str) -> bool:
    return any(term in normalized for term in _DOMAIN_TERMS) or any(
        verb in normalized for verb in _DOMAIN_FOLLOWUP_VERBS
    )


def _has_domain_anchor(context: AgentConversationContext) -> bool:
    """A server-owned or recently-established domain context to inherit from."""

    if context.active_project is not None:
        return True
    return any(
        any(term in (item.content or "") for term in _DOMAIN_TERMS)
        for item in context.recent_messages
    )


def inherits_domain_context(
    message: str, context: AgentConversationContext
) -> bool:
    """Fail-closed domain context inheritance for referential shorthand.

    An otherwise out-of-scope message is admitted only when it is a short
    referential shorthand that positively resolves to the established domain
    context (the server-owned active project or the most recent complete
    domain turn).  A bare ``这个`` attached to an arbitrary non-domain verb
    (translate, compute tax, write an email) does NOT inherit, because it
    carries no positive domain query intent.  Corrections (``不是 A，我说的是
    B``) inherit the prior turn's domain-ness directly.
    """

    normalized = "".join(message.split())
    if not normalized or len(normalized) > 120:
        return False
    if not _is_referential_shorthand(normalized):
        return False
    if not _has_domain_anchor(context):
        return False
    if any(marker in normalized for marker in _CORRECTION_MARKERS):
        return True
    return _has_domain_query(normalized)


__all__ = [
    "ActiveProject",
    "AgentConversationContext",
    "ConversationContextPolicy",
    "ConversationContextService",
    "ConversationMessage",
    "inherits_domain_context",
    "refers_to_active_project",
]
