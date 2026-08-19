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

from .events import open_event_stream
from .models import (
    AgentConversation,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentMessage,
    AgentMessageRole,
)
from .repository import AgentConversationRepository
from .schemas import (
    AgentConversationEnvelope,
    AgentConversationHistory,
    AgentConversationResponse,
    AgentMessageEnvelope,
    AgentMessagePage,
    AgentMessageResponse,
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
                active_config = await session.scalar(
                    select(AgentExecutionConfig).where(
                        AgentExecutionConfig.taskId == active_execution.taskId
                    )
                )
                active_message = (
                    None
                    if active_config is None
                    else await session.get(AgentMessage, active_config.userMessageId)
                )
                if active_message is None:
                    raise RuntimeError("AGENT_EXECUTION_CONFIG_INVALID")
                return AgentConversationEnvelope(
                    conversation=self._conversation(conversation),
                    userMessage=self._message(active_message),
                    streamUrl=f"/api/agent/conversations/{conversation.id}/events",
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
    ) -> AsyncIterator[bytes]:
        return await open_event_stream(
            self._sessions, conversation_id, UUID(identity.user.id), after
        )

    async def history(
        self, identity: SessionIdentity, conversation_id: UUID
    ) -> AgentConversationHistory:
        async with self._sessions() as session:
            conversation = await AgentConversationRepository(session).owned(
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
            messages = await AgentConversationRepository(session).latest_messages(
                conversation.id
            )
        return AgentConversationHistory(
            conversation=self._conversation(conversation),
            messages=[self._message(message) for message in messages],
            nextMessageSequence=conversation.lastMessageSequence + 1,
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
