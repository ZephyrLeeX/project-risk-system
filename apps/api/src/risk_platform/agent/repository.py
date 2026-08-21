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
        """The owner's live conversation, or ``None`` when missing / foreign / hidden.

        A user soft-deleted conversation (``deletedAt``) is indistinguishable
        from a missing one at this layer: every owner-scoped read (history,
        messages, continue, cancel) flows through here and must answer 404
        without leaking that the row still exists for retention/audit.
        """

        return cast(
            AgentConversation | None,
            await self._session.scalar(
                select(AgentConversation).where(
                    AgentConversation.id == conversation_id,
                    AgentConversation.ownerUserId == owner_id,
                    AgentConversation.deletedAt.is_(None),
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

    async def latest_messages(
        self,
        conversation_id: UUID,
        *,
        limit: int = 100,
    ) -> list[AgentMessage]:
        """The most recent ``limit`` messages in ascending sequence order.

        ``messages`` pages forward from a cursor and so returns the *oldest*
        window for a fresh restore; long conversations would otherwise show only
        the earliest 100 turns after a refresh.  This returns the latest window
        (DESC limit, reversed back to ASC) so a restore surfaces the most
        recent USER/ASSISTANT turns the next message continues from.
        """
        rows = (
            await self._session.scalars(
                select(AgentMessage)
                .where(AgentMessage.conversationId == conversation_id)
                .order_by(AgentMessage.sequence.desc())
                .limit(limit)
            )
        ).all()
        return list(reversed(rows))
