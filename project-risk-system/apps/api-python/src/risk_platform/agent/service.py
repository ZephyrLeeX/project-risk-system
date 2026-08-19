"""Agent conversation application service."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.retention.service import RetentionConfigurationRepository
from risk_platform.shared.errors import ApiError

from .events import open_event_stream, request_cancellation
from .interaction import interaction_view
from .models import (
    AgentConversation,
    AgentEvent,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    AgentMessageRole,
    MutationDraft,
)
from .mutations import _display_proposal
from .repository import AgentConversationRepository
from .schemas import (
    AgentConversationEnvelope,
    AgentConversationHistory,
    AgentConversationResponse,
    AgentConversationRuntime,
    AgentInteractionResponse,
    AgentMessageEnvelope,
    AgentMessagePage,
    AgentMessageResponse,
)

_ACTIVE_TASK_STATUSES = (
    DurableTaskStatus.QUEUED,
    DurableTaskStatus.RUNNING,
    DurableTaskStatus.RETRY_WAIT,
)


class AgentConversationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        trace_id: Callable[[], str] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._trace_id = trace_id or (lambda: str(uuid4()))

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Expose the module-owned session dependency without shared app wiring."""
        return self._sessions

    async def create(self, identity: SessionIdentity, message: str) -> AgentConversationEnvelope:
        return await self._create(identity, message, status="created")

    async def continue_conversation(
        self, identity: SessionIdentity, conversation_id: UUID, message: str
    ) -> AgentMessageEnvelope:
        envelope = await self._create(
            identity, message, conversation_id=conversation_id, status="continued"
        )
        return AgentMessageEnvelope(userMessage=envelope.userMessage, streamUrl=envelope.streamUrl)

    async def _create(
        self,
        identity: SessionIdentity,
        message: str,
        *,
        conversation_id: UUID | None = None,
        status: str,
    ) -> AgentConversationEnvelope:
        del status  # The HTTP layer owns 201/202; persistence semantics are identical.
        owner_id = UUID(identity.user.id)
        async with transaction(self._sessions) as session:
            repository = AgentConversationRepository(session)
            conversation = (
                await repository.owned(conversation_id, owner_id)
                if conversation_id is not None
                else None
            )
            if conversation_id is not None and conversation is None:
                raise ApiError(
                    404, "AGENT_CONVERSATION_NOT_FOUND", "Agent 会话不存在或不属于当前用户"
                )
            if conversation is None:
                now = datetime.now(UTC)
                retention = await RetentionConfigurationRepository(session).current()
                conversation = AgentConversation(
                    ownerUserId=owner_id,
                    createdAt=now,
                    updatedAt=now,
                    expiresAt=retention.conversation_expires_at(now),
                    retentionConfigVersion=retention.version,
                )
                session.add(conversation)
                await session.flush()
            else:
                conversation = await session.scalar(
                    select(AgentConversation)
                    .where(AgentConversation.id == conversation.id)
                    .with_for_update()
                )
                if conversation is None:
                    raise ApiError(
                        404, "AGENT_CONVERSATION_NOT_FOUND", "Agent 会话不存在或不属于当前用户"
                    )
            active_execution = await session.scalar(
                select(AgentExecution)
                .join(DurableTask, DurableTask.id == AgentExecution.taskId)
                .where(
                    AgentExecution.conversationId == conversation.id,
                    (
                        AgentExecution.status == AgentExecutionStatus.WAITING_FOR_USER
                    )
                    | (
                        (AgentExecution.status == AgentExecutionStatus.RUNNING)
                        & DurableTask.status.in_(
                            (
                                DurableTaskStatus.QUEUED,
                                DurableTaskStatus.RUNNING,
                                DurableTaskStatus.RETRY_WAIT,
                            )
                        )
                    ),
                )
                .with_for_update()
            )
            if active_execution is not None:
                # An execution is still live (RUNNING with an active durable task
                # or WAITING_FOR_USER).  A new message must not be silently
                # de-duplicated against the prior turn's userMessage: the UI
                # restores the live turn via ``history.runtime`` or cancels it
                # explicitly via POST /cancel.  Returning the stale envelope
                # here would make a refresh look like a successful new send.
                raise ApiError(
                    409,
                    "AGENT_EXECUTION_ACTIVE",
                    "当前对话仍有进行中的执行，请先恢复或取消",  # noqa: RUF001
                )
            user_message = AgentMessage(
                conversationId=conversation.id,
                sequence=conversation.lastMessageSequence + 1,
                role=AgentMessageRole.USER,
                content=message,
                traceId=self._trace_id(),
                dataAsOf=datetime.now(UTC),
            )
            session.add(user_message)
            await session.flush()
            config_id = uuid4()
            task = await enqueue_task(
                session,
                DurableTaskKind.AGENT_EXECUTION,
                f"agent-execution:{conversation.id}:{user_message.id}",
                {
                    "conversation_id": str(conversation.id),
                    "user_message_id": str(user_message.id),
                    "requested_by_user_id": str(owner_id),
                    "execution_configuration_id": str(config_id),
                },
            )
            execution = AgentExecution(
                conversationId=conversation.id,
                taskId=task.id,
                userMessageId=user_message.id,
                requestedByUserId=owner_id,
                status=AgentExecutionStatus.RUNNING,
            )
            session.add(execution)
            session.add(
                AgentExecutionConfig(
                    id=config_id,
                    taskId=task.id,
                    conversationId=conversation.id,
                    userMessageId=user_message.id,
                    requestedByUserId=owner_id,
                    providerConfigId=None,
                    providerNameSnapshot=None,
                    endpointSnapshot=None,
                    protocolSnapshot=None,
                    modelSnapshot=None,
                    encryptedApiKeySnapshot=None,
                    timeoutSeconds=90,
                )
            )
            await session.refresh(conversation)
            await session.refresh(user_message)
        return AgentConversationEnvelope(
            conversation=self._conversation(conversation),
            userMessage=self._message(user_message),
            streamUrl=f"/api/agent/conversations/{conversation.id}/events",
        )

    async def events(
        self,
        identity: SessionIdentity,
        conversation_id: UUID,
        after: UUID | None,
        after_sequence: int | None,
    ) -> AsyncIterator[bytes]:
        return await open_event_stream(
            self._sessions,
            conversation_id,
            UUID(identity.user.id),
            after,
            after_sequence=after_sequence,
        )

    async def history(
        self, identity: SessionIdentity, conversation_id: UUID
    ) -> AgentConversationHistory:
        async with self._sessions() as session:
            repository = AgentConversationRepository(session)
            conversation = await repository.owned(
                conversation_id, UUID(identity.user.id)
            )
            if conversation is None:
                raise ApiError(
                    404, "AGENT_CONVERSATION_NOT_FOUND", "Agent 会话不存在或不属于当前用户"
                )
            # Restore the *latest* window, not the oldest 100: a forward-paged
            # oldest window would hide the most recent turns of a long
            # conversation after a refresh.  nextMessageSequence still reflects
            # the true tail so the next send continues the same conversation.
            messages = await repository.latest_messages(conversation.id)
            runtime = await self._runtime(session, conversation.id)
        return AgentConversationHistory(
            conversation=self._conversation(conversation),
            messages=[self._message(message) for message in messages],
            nextMessageSequence=conversation.lastMessageSequence + 1,
            runtime=runtime,
        )

    async def message_page(
        self,
        identity: SessionIdentity,
        conversation_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> AgentMessagePage:
        async with self._sessions() as session:
            conversation = await AgentConversationRepository(session).owned(
                conversation_id, UUID(identity.user.id)
            )
            if conversation is None:
                raise ApiError(
                    404, "AGENT_CONVERSATION_NOT_FOUND", "Agent 会话不存在或不属于当前用户"
                )
            messages = await AgentConversationRepository(session).messages(
                conversation.id, after_sequence=after_sequence, limit=limit
            )
        next_after = messages[-1].sequence if messages else after_sequence
        return AgentMessagePage(
            items=[self._message(message) for message in messages],
            nextAfterSequence=next_after,
        )

    async def cancel(
        self, identity: SessionIdentity, conversation_id: UUID
    ) -> AgentConversationRuntime:
        """Set the explicit cancel flag on the live execution and return its runtime.

        This is the only path that calls ``request_cancellation``: a transport
        disconnect (refresh / tab close / network drop) no longer cancels, so a
        user must opt in.  Owner-scoped — a conversation that is not owned is a
        404 before any state is touched.
        """

        owner_id = UUID(identity.user.id)
        async with self._sessions() as session:
            conversation = await AgentConversationRepository(session).owned(
                conversation_id, owner_id
            )
            if conversation is None:
                raise ApiError(
                    404, "AGENT_CONVERSATION_NOT_FOUND", "Agent 会话不存在或不属于当前用户"
                )
        await request_cancellation(self._sessions, conversation_id)
        async with self._sessions() as session:
            runtime = await self._runtime(session, conversation_id)
        if runtime is not None:
            return runtime
        # Nothing was active when the cancel landed (the turn already reached a
        # terminal status between the UI affordance and the request).  Surface
        # the latest execution's terminal status so the caller can sync instead
        # of erroring on the race.
        async with self._sessions() as session:
            execution = await session.scalar(
                select(AgentExecution)
                .where(AgentExecution.conversationId == conversation_id)
                .order_by(AgentExecution.createdAt.desc())
                .limit(1)
            )
        if execution is None:
            raise ApiError(409, "AGENT_NO_ACTIVE_EXECUTION", "当前对话没有进行中的执行")
        return AgentConversationRuntime(
            status=execution.status.value, streamUrl=None, interaction=None
        )

    async def _runtime(
        self, session: AsyncSession, conversation_id: UUID
    ) -> AgentConversationRuntime | None:
        """Snapshot the latest execution's live state for restore-on-refresh.

        ``None`` when no execution is active, which keeps the happy-path restore
        (``status == "completed"``) unchanged.  An execution is active when it
        is ``WAITING_FOR_USER`` (restore the OPEN interaction) or ``RUNNING``
        with a durable task still in a non-terminal status (reattach the
        stream); a ``RUNNING`` row whose task already terminated is stale and
        treated as not-active so a refresh cannot reconnect-loop on a crashed
        worker.
        """

        execution = await session.scalar(
            select(AgentExecution)
            .where(AgentExecution.conversationId == conversation_id)
            .order_by(AgentExecution.createdAt.desc())
            .limit(1)
        )
        if execution is None:
            return None
        if execution.status is AgentExecutionStatus.WAITING_FOR_USER:
            return AgentConversationRuntime(
                status=AgentExecutionStatus.WAITING_FOR_USER.value,
                streamUrl=None,
                interaction=await self._open_interaction(session, execution.id),
            )
        if execution.status is AgentExecutionStatus.RUNNING:
            task_status = await session.scalar(
                select(DurableTask.status).where(DurableTask.id == execution.taskId)
            )
            if task_status in _ACTIVE_TASK_STATUSES:
                # Snapshot the durable per-conversation tail so the restore
                # reconnects the stream *from this sequence cursor*
                # (``?afterSequence=<n>``), not from the request-time tail the
                # SSE GET would otherwise re-read.  The sequence is always
                # defined (a fresh conversation is 0), so unlike the event-id
                # cursor it closes the zero-event race where a brand-new first
                # turn has written no AgentEvent yet.  See
                # ``AgentConversationRuntime`` for the terminal-event race.
                last_event_sequence = await session.scalar(
                    select(AgentConversation.lastEventSequence).where(
                        AgentConversation.id == conversation_id
                    )
                )
                resume_after_event_id = await session.scalar(
                    select(AgentEvent.id)
                    .where(AgentEvent.conversationId == conversation_id)
                    .order_by(AgentEvent.sequence.desc())
                    .limit(1)
                )
                # The cancel flag lives on the execution configuration (ADR 0028):
                # the worker observes it at its next heartbeat boundary, so the
                # execution is still RUNNING for a window after POST /cancel.
                # Exposing it lets a restore stay ``cancelling`` instead of
                # reopening the normal stream — the status enum has no
                # ``CANCELLING`` value per ADR 0036.
                cancellation_requested_at = await session.scalar(
                    select(AgentExecutionConfig.cancellationRequestedAt).where(
                        AgentExecutionConfig.taskId == execution.taskId
                    )
                )
                return AgentConversationRuntime(
                    status=AgentExecutionStatus.RUNNING.value,
                    streamUrl=f"/api/agent/conversations/{conversation_id}/events",
                    interaction=None,
                    resumeAfterEventId=resume_after_event_id,
                    resumeAfterEventSequence=int(last_event_sequence or 0),
                    cancellationRequested=cancellation_requested_at is not None,
                )
        return None

    async def _open_interaction(
        self, session: AsyncSession, execution_id: UUID
    ) -> AgentInteractionResponse | None:
        """Restore the OPEN interaction (project selection or write confirmation)."""

        interaction = await session.scalar(
            select(AgentInteraction)
            .where(
                AgentInteraction.executionId == execution_id,
                AgentInteraction.status == AgentInteractionStatus.OPEN,
            )
            .order_by(AgentInteraction.createdAt.desc())
            .limit(1)
        )
        if interaction is None:
            return None
        view = interaction_view(interaction)
        if interaction.type is AgentInteractionType.WRITE_CONFIRMATION:
            draft = await session.scalar(
                select(MutationDraft).where(MutationDraft.interactionId == interaction.id)
            )
            view.draft = (
                None if draft is None else await _display_proposal(session, draft.proposal)
            )
        return view

    @staticmethod
    def _conversation(value: AgentConversation) -> AgentConversationResponse:
        return AgentConversationResponse(
            id=value.id,
            createdAt=value.createdAt,
            updatedAt=value.updatedAt,
            expiresAt=value.expiresAt,
            lastMessageSequence=value.lastMessageSequence,
            lastEventSequence=value.lastEventSequence,
        )

    @staticmethod
    def _message(value: AgentMessage) -> AgentMessageResponse:
        return AgentMessageResponse(
            id=value.id,
            sequence=value.sequence,
            role=value.role.value,
            content=value.content,
            structured=value.structured,
            traceId=value.traceId,
            dataAsOf=value.dataAsOf,
            createdAt=value.createdAt,
        )


__all__ = ["AgentConversationService"]
