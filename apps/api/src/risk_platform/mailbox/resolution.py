"""Mailbox-owned human resolution and resume boundary."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.mailbox.models import (
    MailMessage,
    MailMessageProjectMatch,
    MailMessageStatus,
    MailProjectMatchType,
    MailProjectResolutionStatus,
    MailSourceHandoff,
)
from risk_platform.rbac.scopes import get_scoped_project
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.shared.errors import ApiError


class MailProjectResolutionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def confirm(
        self, message_id: UUID, project_id: UUID, identity: SessionIdentity
    ) -> None:
        owner_id = UUID(identity.user.id)
        async with transaction(self._sessions) as session:
            message = await session.scalar(
                select(MailMessage).where(MailMessage.id == message_id).with_for_update()
            )
            if message is None:
                raise ApiError(404, "MAIL_MESSAGE_NOT_FOUND", "邮件不存在")
            project = await get_scoped_project(
                session, project_id, owner_id, identity.user.dataScope
            )
            if project is None:
                raise ApiError(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问")
            if message.projectResolutionStatus not in {
                MailProjectResolutionStatus.WAITING_CONFIRMATION,
                MailProjectResolutionStatus.PENDING,
            }:
                if (
                    message.projectResolutionStatus is MailProjectResolutionStatus.CONFIRMED
                    and message.resolvedProjectId == project_id
                ):
                    return
                raise ApiError(409, "MAIL_PROJECT_RESOLUTION_ALREADY_DONE", "邮件项目识别已处理")
            message.projectResolutionStatus = MailProjectResolutionStatus.CONFIRMED
            message.resolvedProjectId = project.id
            message.projectResolutionConfirmedById = owner_id
            message.status = MailMessageStatus.ANALYZING
            match = await session.scalar(
                select(MailMessageProjectMatch)
                .where(
                    MailMessageProjectMatch.messageId == message.id,
                    MailMessageProjectMatch.projectId == project.id,
                )
                .with_for_update()
            )
            if match is None:
                session.add(
                    MailMessageProjectMatch(
                        messageId=message.id,
                        projectId=project.id,
                        matchType=MailProjectMatchType.MANUAL,
                        confidence=100,
                        matchedText=project.name[:500],
                        confirmedById=owner_id,
                    )
                )
            else:
                match.matchType = MailProjectMatchType.MANUAL
                match.confidence = 100
                match.confirmedById = owner_id
            handoff = await session.scalar(
                select(MailSourceHandoff).where(MailSourceHandoff.messageId == message.id)
            )
            if handoff is None:
                raise ApiError(409, "MAIL_HANDOFF_NOT_FOUND", "邮件处理上下文不存在")
            await enqueue_task(
                session,
                DurableTaskKind.MAIL_AI_REVIEW_PUBLISH,
                f"mail-ai:{message.mailboxConfigId}:{message.uidValidity}:{message.imapUid}",
                {
                    "mailbox_config_id": str(message.mailboxConfigId),
                    "uid_validity": message.uidValidity,
                    "imap_uid": message.imapUid,
                },
            )


__all__ = ["MailProjectResolutionService"]
