"""ADR 0022 source-refetch worker for T025 parsing and project matching."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.ai_providers.v2_service import ProviderV2Runtime
from risk_platform.mailbox.ai import MailboxProviderV2
from risk_platform.mailbox.connection import MailboxConnection, MailSourceUnavailable
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailMessage,
    MailMessageProjectMatch,
    MailMessageStatus,
    MailProjectMatchType,
    MailProjectResolutionStatus,
    MailSourceHandoff,
    MailStageStatus,
)
from risk_platform.mailbox.parsing import MailParseError, cleanup_stale_temp_directories, parse_mail
from risk_platform.model_types import JSONValue
from risk_platform.projects.resolution_service import ProjectResolutionService
from risk_platform.rbac.models import DataScopeType, Role, UserRole
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTask, DurableTaskKind
from risk_platform.shared.crypto import LegacySecretFields, SecretCipher, SecretCryptoError
from risk_platform.weekly_reports.service import invalidate_message_project


class MailParseWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        connection: MailboxConnection | None = None,
        provider_runtime: ProviderV2Runtime | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._connection = connection or MailboxConnection()
        self._resolution = ProjectResolutionService()
        self._provider = MailboxProviderV2(provider_runtime) if provider_runtime else None
        cleanup_stale_temp_directories()

    async def handle(self, payload: Mapping[str, object]) -> None:
        cleanup_stale_temp_directories()
        mailbox_id = UUID(str(payload["mailbox_config_id"]))
        uid_validity = int(str(payload["uid_validity"]))
        imap_uid = int(str(payload["imap_uid"]))
        try:
            source, fallback_id = await self._refetch(mailbox_id, uid_validity, imap_uid)
            parsed = parse_mail(source, fallback_id)
        except MailParseError as exc:
            await self._terminal(mailbox_id, uid_validity, imap_uid, exc.code)
            return
        except MailSourceUnavailable as exc:
            await self._terminal(mailbox_id, uid_validity, imap_uid, str(exc))
            return
        except Exception:
            if await self._exhausted(mailbox_id, uid_validity, imap_uid):
                await self._terminal(mailbox_id, uid_validity, imap_uid, "PARSER_RETRY_EXHAUSTED")
                return
            await self._retryable(mailbox_id, uid_validity, imap_uid, "PARSER_RUNTIME_FAILURE")
            raise
        async with self._session_factory() as session, session.begin():
            handoff = await self._handoff(session, mailbox_id, uid_validity, imap_uid)
            if handoff is None:
                return
            existing = await session.scalar(
                select(MailMessage)
                .where(
                    MailMessage.mailboxConfigId == mailbox_id,
                    MailMessage.uidValidity == uid_validity,
                    MailMessage.imapUid == imap_uid,
                )
                .with_for_update()
            )
            if existing is None:
                message = MailMessage(
                    mailboxConfigId=mailbox_id,
                    batchId=handoff.batchId,
                    messageId=parsed.message_id,
                    uidValidity=uid_validity,
                    imapUid=imap_uid,
                    subject=parsed.subject,
                    senderName=parsed.sender_name,
                    senderAddress=parsed.sender_address,
                    sentAt=handoff.sentAt,
                    receivedAt=handoff.receivedAt,
                    receivedAtSource=handoff.receivedAtSource,
                )
                session.add(message)
                await session.flush()
            else:
                message = existing
            message.sanitizedSummary = parsed.summary
            message.keyPoints = cast(JSONValue, parsed.key_points)
            message.attachmentMetadata = cast(JSONValue, parsed.attachment_metadata)
            message.status = MailMessageStatus.ANALYZING
            message.processedAt = None
            message.failureCode = message.failureSummary = None
            old_project_ids = set(
                await session.scalars(
                    select(MailMessageProjectMatch.projectId).where(
                        MailMessageProjectMatch.messageId == message.id
                    )
                )
            )
            await session.execute(
                delete(MailMessageProjectMatch).where(
                    MailMessageProjectMatch.messageId == message.id
                )
            )
            mailbox_config = await session.get(MailboxConfig, mailbox_id)
            user = await session.get(User, mailbox_config.userId) if mailbox_config else None
            scope = await self._scope(session, user.id if user else None)
            resolution = await self._resolution.resolve(
                session,
                parsed.subject,
                parsed.text,
                user.id if user else mailbox_id,
                scope,
                self._provider.resolve_project if self._provider else None,
            )
            if resolution.project_id is not None:
                selected = next(
                    item
                    for item in resolution.candidates
                    if item.project_id == resolution.project_id
                )
                session.add(
                    MailMessageProjectMatch(
                        messageId=message.id,
                        projectId=selected.project_id,
                        matchType=(
                            MailProjectMatchType.FUZZY
                            if resolution.source == "AI"
                            else MailProjectMatchType.EXACT
                        ),
                        confidence=resolution.confidence or 0,
                        matchedText=selected.name[:500],
                    )
                )
            message.projectResolutionStatus = (
                MailProjectResolutionStatus.AUTO_MATCH
                if resolution.project_id is not None
                else MailProjectResolutionStatus.WAITING_CONFIRMATION
            )
            message.resolvedProjectId = resolution.project_id
            message.projectResolutionConfidence = resolution.confidence
            message.projectResolutionCandidates = cast(
                JSONValue,
                [
                    {
                        "optionId": item.option_id,
                        "projectId": str(item.project_id),
                        "name": item.name,
                        "externalCode": item.external_code,
                        "alias": item.alias,
                        "status": item.status,
                    }
                    for item in resolution.candidates
                ],
            )
            if resolution.project_id is None:
                message.failureCode = message.failureSummary = "WAITING_PROJECT_CONFIRMATION"
            affected_projects = old_project_ids | (
                {resolution.project_id} if resolution.project_id is not None else set()
            )
            for project_id in sorted(affected_projects, key=str):
                await invalidate_message_project(session, message, project_id)
            handoff.parseStatus = MailStageStatus.SUCCEEDED
            handoff.failureCode = handoff.failureSummary = None
            if resolution.project_id is not None:
                await enqueue_task(
                    session,
                    DurableTaskKind.MAIL_AI_REVIEW_PUBLISH,
                    f"mail-ai:{mailbox_id}:{uid_validity}:{imap_uid}",
                    {
                        "mailbox_config_id": str(mailbox_id),
                        "uid_validity": uid_validity,
                        "imap_uid": imap_uid,
                    },
                )

    async def _scope(self, session: AsyncSession, user_id: UUID | None) -> DataScopeType:
        if user_id is None:
            return DataScopeType.NONE
        scopes = list(
            await session.scalars(
                select(UserRole.dataScope)
                .join(Role, Role.id == UserRole.roleId)
                .where(UserRole.userId == user_id, Role.enabled.is_(True))
            )
        )
        order = {
            DataScopeType.ALL: 5,
            DataScopeType.OWNED_OR_ASSIGNED: 4,
            DataScopeType.OWNED: 3,
            DataScopeType.ASSIGNED: 2,
            DataScopeType.NONE: 1,
        }
        return max(scopes, key=lambda item: order[DataScopeType(item)], default=DataScopeType.NONE)

    async def _refetch(
        self, mailbox_id: UUID, uid_validity: int, imap_uid: int
    ) -> tuple[bytes, str]:
        async with self._session_factory() as session:
            config = await session.get(MailboxConfig, mailbox_id)
            if config is None:
                raise MailParseError("MAIL_SOURCE_MISSING")
            if config.uidValidity != uid_validity:
                raise MailParseError("UIDVALIDITY_CHANGED")
            try:
                auth = self._cipher.decrypt_legacy(
                    LegacySecretFields(
                        config.encryptedAuthCode, config.authCodeIv, config.authCodeTag, "v1"
                    )
                )
            except SecretCryptoError:
                raise MailParseError("MAIL_SOURCE_MISSING") from None
            source = await self._connection.fetch_source(
                email=config.email,
                auth_code=auth,
                host=config.imapHost,
                port=config.imapPort,
                encryption=config.encryption.value,
                folder=config.folder,
                uid_validity=uid_validity,
                imap_uid=imap_uid,
            )
            return source, f"<imap-{mailbox_id}-{uid_validity}-{imap_uid}>"

    async def _handoff(
        self, session: AsyncSession, mailbox_id: UUID, uid_validity: int, imap_uid: int
    ) -> MailSourceHandoff | None:
        return cast(
            MailSourceHandoff | None,
            await session.scalar(
                select(MailSourceHandoff)
                .where(
                    MailSourceHandoff.mailboxConfigId == mailbox_id,
                    MailSourceHandoff.uidValidity == uid_validity,
                    MailSourceHandoff.imapUid == imap_uid,
                )
                .with_for_update()
            ),
        )

    async def _terminal(
        self, mailbox_id: UUID, uid_validity: int, imap_uid: int, code: str
    ) -> None:
        async with self._session_factory() as session, session.begin():
            handoff = await self._handoff(session, mailbox_id, uid_validity, imap_uid)
            if handoff is not None:
                handoff.parseStatus = MailStageStatus.PERMANENT_FAILURE
                handoff.failureCode, handoff.failureSummary = code, code
                message = await self._message(session, mailbox_id, uid_validity, imap_uid)
                if message is not None:
                    message.status = MailMessageStatus.FAILED
                    message.failureCode, message.failureSummary = code, code

    async def _retryable(
        self, mailbox_id: UUID, uid_validity: int, imap_uid: int, code: str
    ) -> None:
        async with self._session_factory() as session, session.begin():
            handoff = await self._handoff(session, mailbox_id, uid_validity, imap_uid)
            if handoff is not None:
                handoff.parseStatus = MailStageStatus.RETRYABLE_FAILURE
                handoff.failureCode, handoff.failureSummary = code, code
                message = await self._message(session, mailbox_id, uid_validity, imap_uid)
                if message is not None:
                    message.status = MailMessageStatus.ANALYZING
                    message.failureCode, message.failureSummary = code, code

    @staticmethod
    async def _message(
        session: AsyncSession, mailbox_id: UUID, uid_validity: int, imap_uid: int
    ) -> MailMessage | None:
        return cast(
            MailMessage | None,
            await session.scalar(
                select(MailMessage)
                .where(
                    MailMessage.mailboxConfigId == mailbox_id,
                    MailMessage.uidValidity == uid_validity,
                    MailMessage.imapUid == imap_uid,
                )
                .with_for_update()
            ),
        )

    async def _exhausted(self, mailbox_id: UUID, uid_validity: int, imap_uid: int) -> bool:
        async with self._session_factory() as session:
            handoff = await self._handoff(session, mailbox_id, uid_validity, imap_uid)
            if handoff is None:
                return True
            task = await session.get(DurableTask, handoff.parseTaskId)
            return task is not None and task.attemptCount >= task.maxAttempts


__all__ = ["MailParseWorker"]
