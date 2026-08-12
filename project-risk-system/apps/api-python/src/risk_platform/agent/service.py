"""Agent conversation application service."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.models import AiConnectionStatus, AiProviderConfig
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.retention.service import RetentionConfigurationRepository
from risk_platform.shared.errors import ApiError

from .events import open_event_stream
from .models import AgentConversation, AgentExecutionConfig, AgentMessage, AgentMessageRole
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
            active = await session.scalar(
                select(DurableTask).where(
                    DurableTask.kind == DurableTaskKind.AGENT_EXECUTION,
                    DurableTask.status.in_(
                        (
                            DurableTaskStatus.QUEUED,
                            DurableTaskStatus.RUNNING,
                            DurableTaskStatus.RETRY_WAIT,
                        )
                    ),
                    DurableTask.payload["conversation_id"].as_string() == str(conversation.id),
                )
            )
            if active is not None:
                active_config = await session.scalar(
                    select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == active.id)
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
            provider = await session.scalar(
                select(AiProviderConfig)
                .where(
                    AiProviderConfig.enabled.is_(True),
                    AiProviderConfig.isDefault.is_(True),
                    AiProviderConfig.lastTestStatus == AiConnectionStatus.HEALTHY,
                    or_(
                        AiProviderConfig.expiresAt.is_(None),
                        AiProviderConfig.expiresAt >= date.today(),
                    ),
                )
                .order_by(AiProviderConfig.priority, AiProviderConfig.id)
                .limit(1)
            )
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
            session.add(
                AgentExecutionConfig(
                    id=config_id,
                    taskId=task.id,
                    conversationId=conversation.id,
                    userMessageId=user_message.id,
                    requestedByUserId=owner_id,
                    providerConfigId=provider.id if provider else None,
                    providerNameSnapshot=provider.name if provider else None,
                    endpointSnapshot=provider.endpoint if provider else None,
                    modelSnapshot=provider.model if provider else None,
                    encryptedApiKeySnapshot=provider.encryptedApiKey if provider else None,
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
            messages = await AgentConversationRepository(session).messages(conversation.id)
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
            traceId=value.traceId,
            dataAsOf=value.dataAsOf,
            createdAt=value.createdAt,
        )


__all__ = ["AgentConversationService"]
