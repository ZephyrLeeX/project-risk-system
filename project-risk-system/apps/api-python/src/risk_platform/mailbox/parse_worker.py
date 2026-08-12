"""ADR 0022 source-refetch worker for T025 parsing and project matching."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.mailbox.connection import MailboxConnection, MailSourceUnavailable
from risk_platform.mailbox.matching import MailProjectMatcher
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailMessage,
    MailMessageProjectMatch,
    MailMessageStatus,
    MailSourceHandoff,
    MailStageStatus,
)
from risk_platform.mailbox.parsing import MailParseError, cleanup_stale_temp_directories, parse_mail
from risk_platform.model_types import JSONValue
from risk_platform.reliability.models import DurableTask
from risk_platform.shared.crypto import LegacySecretFields, SecretCipher, SecretCryptoError


class MailParseWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        connection: MailboxConnection | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._connection = connection or MailboxConnection()
        self._matcher = MailProjectMatcher()
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
                .where(MailMessage.mailboxConfigId == mailbox_id, MailMessage.imapUid == imap_uid)
                .with_for_update()
            )
            if existing is None:
                message = MailMessage(
                    mailboxConfigId=mailbox_id,
                    batchId=handoff.batchId,
                    messageId=parsed.message_id,
                    imapUid=imap_uid,
                    subject=parsed.subject,
                    senderName=parsed.sender_name,
                    senderAddress=parsed.sender_address,
                    sentAt=cast(datetime | None, parsed.sent_at),
                )
                session.add(message)
                await session.flush()
            else:
                message = existing
            message.sanitizedSummary = parsed.summary
            message.keyPoints = cast(JSONValue, parsed.key_points)
            message.attachmentMetadata = cast(JSONValue, parsed.attachment_metadata)
            message.status = MailMessageStatus.COMPLETED
            message.processedAt = datetime.now(UTC)
            message.failureCode = message.failureSummary = None
            await session.execute(
                delete(MailMessageProjectMatch).where(
                    MailMessageProjectMatch.messageId == message.id
                )
            )
            for match in await self._matcher.match(session, parsed.subject, parsed.text):
                session.add(
                    MailMessageProjectMatch(
                        messageId=message.id,
                        projectId=match.project_id,
                        matchType=match.match_type,
                        confidence=match.confidence,
                        matchedText=match.matched_text,
                    )
                )
            handoff.parseStatus = MailStageStatus.SUCCEEDED
            handoff.failureCode = handoff.failureSummary = None

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

    async def _retryable(
        self, mailbox_id: UUID, uid_validity: int, imap_uid: int, code: str
    ) -> None:
        async with self._session_factory() as session, session.begin():
            handoff = await self._handoff(session, mailbox_id, uid_validity, imap_uid)
            if handoff is not None:
                handoff.parseStatus = MailStageStatus.RETRYABLE_FAILURE
                handoff.failureCode, handoff.failureSummary = code, code

    async def _exhausted(self, mailbox_id: UUID, uid_validity: int, imap_uid: int) -> bool:
        async with self._session_factory() as session:
            handoff = await self._handoff(session, mailbox_id, uid_validity, imap_uid)
            if handoff is None:
                return True
            task = await session.get(DurableTask, handoff.parseTaskId)
            return task is not None and task.attemptCount >= task.maxAttempts


__all__ = ["MailParseWorker"]
