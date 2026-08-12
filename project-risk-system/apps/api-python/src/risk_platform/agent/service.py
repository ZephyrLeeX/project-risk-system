"""Agent conversation application service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.retention.service import RetentionConfigurationRepository
from risk_platform.shared.errors import ApiError

from .models import AgentConversation, AgentMessage, AgentMessageRole
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
            await session.refresh(conversation)
            await session.refresh(user_message)
        return AgentConversationEnvelope(
            conversation=self._conversation(conversation),
            userMessage=self._message(user_message),
            streamUrl=f"/api/agent/conversations/{conversation.id}/events",
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
