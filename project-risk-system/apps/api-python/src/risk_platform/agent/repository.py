"""Owner-scoped Agent conversation persistence."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentConversation, AgentMessage


class AgentConversationRepository:
    """Keep conversation reads narrow and owner-bound."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owned(self, conversation_id: UUID, owner_id: UUID) -> AgentConversation | None:
        return cast(
            AgentConversation | None,
            await self._session.scalar(
                select(AgentConversation).where(
                    AgentConversation.id == conversation_id,
                    AgentConversation.ownerUserId == owner_id,
                )
            ),
        )

    async def messages(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[AgentMessage]:
        return list(
            (
                await self._session.scalars(
                    select(AgentMessage)
                    .where(
                        AgentMessage.conversationId == conversation_id,
                        AgentMessage.sequence > after_sequence,
                    )
                    .order_by(AgentMessage.sequence)
                    .limit(limit)
                )
            ).all()
        )
