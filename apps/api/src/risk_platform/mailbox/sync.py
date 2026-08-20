"""Durable, UID-only IMAP synchronization orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.db import transaction
from risk_platform.mailbox.connection import MailboxConnection, MailEnvelope, MailSyncSnapshot
from risk_platform.mailbox.filtering import (
    DEFAULT_WEEKLY_REPORT_KEYWORDS,
    MailCandidateFilter,
    MailCandidateFilterConfig,
)
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailMessage,
    MailMessageSkipReason,
    MailMessageStatus,
    MailReceivedAtSource,
    MailRiskCandidate,
    MailSourceHandoff,
    MailStageStatus,
    MailSyncBatch,
    MailSyncStatus,
    MailSyncTrigger,
)
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.shared.crypto import LegacySecretFields, SecretCipher, SecretCryptoError

MAX_BATCH_UIDS: Final = 50
TERMINAL: Final = frozenset({MailStageStatus.SUCCEEDED, MailStageStatus.PERMANENT_FAILURE})


class MailSyncError(RuntimeError):
    """Safe, structured synchronization failure."""

    def __init__(self, code: str, summary: str) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary


class MailboxSyncService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        connection: MailboxConnection | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._connection = connection or MailboxConnection()

    async def run(self, batch_id: UUID) -> None:
        """Run one batch. All durable facts are written without source content."""

        async with transaction(self._session_factory) as session:
            batch = await session.scalar(
                select(MailSyncBatch).where(MailSyncBatch.id == batch_id).with_for_update()
            )
            if batch is None or batch.status != MailSyncStatus.QUEUED:
                return
            active = await session.scalar(
                select(MailSyncBatch.id)
                .where(
                    MailSyncBatch.mailboxConfigId == batch.mailboxConfigId,
                    MailSyncBatch.status.in_([MailSyncStatus.QUEUED, MailSyncStatus.RUNNING]),
                    MailSyncBatch.id != batch.id,
                )
                .with_for_update()
            )
            if active is not None:
                return
            config = await session.scalar(
                select(MailboxConfig)
                .where(MailboxConfig.id == batch.mailboxConfigId)
                .with_for_update()
            )
            if config is None:
                raise MailSyncError("MAILBOX_NOT_FOUND", "邮箱配置不存在")
            batch.status = MailSyncStatus.RUNNING
            batch.startedAt = datetime.now(UTC)

        try:
            snapshot = await self._discover(config)
            candidate_filter = self._candidate_filter(config)
            envelopes = snapshot.envelopes[:MAX_BATCH_UIDS]
            candidates: list[MailEnvelope] = []
            skipped_uids: list[int] = []
            for envelope in envelopes:
                if candidate_filter.evaluate(subject=envelope.subject, sender=envelope.sender):
                    candidates.append(envelope)
                else:
                    skipped_uids.append(envelope.uid)
            async with transaction(self._session_factory) as session:
                current = await session.scalar(
                    select(MailboxConfig).where(MailboxConfig.id == config.id).with_for_update()
                )
                batch = await session.scalar(
                    select(MailSyncBatch).where(MailSyncBatch.id == batch_id).with_for_update()
                )
                assert current is not None and batch is not None
                if current.uidValidity is not None and current.uidValidity != snapshot.uid_validity:
                    current.uidValidity = snapshot.uid_validity
                    current.uidCursor = None
                    batch.uidValidity = snapshot.uid_validity
                    batch.status = MailSyncStatus.FAILURE
                    batch.errorSummary = "UIDVALIDITY已变化, 已重置游标; 请执行重新基线"
                    batch.finishedAt = datetime.now(UTC)
                    batch.failedCount = 1
                    return
                current.uidValidity = snapshot.uid_validity
                batch.uidValidity = snapshot.uid_validity
                # One-time safe cleanup: re-judge this mailbox's historical
                # MailMessages against the candidate filter. Messages now
                # judged non-weekly that never produced a risk candidate are
                # marked SKIPPED + FILTERED and hidden from the default list
                # — never physically deleted, and never touched once a formal
                # risk candidate exists for them.
                await self._reclassify_historical(session, current, candidate_filter)
                for envelope in candidates:
                    await self._handoff(session, batch, current, envelope)
                batch.discoveredCount = min(len(snapshot.envelopes), MAX_BATCH_UIDS)
                # The scanned cursor must advance over *every* scanned UID, not
                # only accepted weekly-report candidates — otherwise non-weekly
                # mail (AD expiry alerts, verification codes) would be re-scanned
                # on every sync. ``endUid`` is the highest scanned UID this
                # batch observed; the durable cursor advances to it once the
                # batch's downstream stages reach a terminal state.
                scanned_uids = [envelope.uid for envelope in envelopes]
                if scanned_uids:
                    batch.startUid = min(scanned_uids)
                    batch.endUid = max(scanned_uids)
                batch.skippedCount = len(skipped_uids)
                await self._refresh_batch(session, batch, current)
        except MailSyncError as exc:
            await self._fail_batch(batch_id, exc)
            raise
        except Exception as exc:
            error = MailSyncError("IMAP_SYNC_FAILED", "IMAP同步失败, 请稍后重试")
            await self._fail_batch(batch_id, error)
            raise exc from None

    async def run_for_config(self, mailbox_config_id: UUID) -> None:
        async with self._session_factory() as session:
            batch_id = await session.scalar(
                select(MailSyncBatch.id)
                .where(
                    MailSyncBatch.mailboxConfigId == mailbox_config_id,
                    MailSyncBatch.status == MailSyncStatus.QUEUED,
                )
                .order_by(MailSyncBatch.createdAt)
                .limit(1)
            )
        if batch_id is not None:
            await self.run(batch_id)

    async def rebaseline(
        self, mailbox_config_id: UUID, uid_validity: int, cursor: int | None = None
    ) -> None:
        """Explicitly establish a new UIDVALIDITY baseline after a reset."""

        if uid_validity <= 0 or (cursor is not None and cursor < 0):
            raise ValueError("uid_validity and cursor must be positive/non-negative")
        async with transaction(self._session_factory) as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.id == mailbox_config_id).with_for_update()
            )
            if config is None:
                raise MailSyncError("MAILBOX_NOT_FOUND", "邮箱配置不存在")
            config.uidValidity = uid_validity
            config.uidCursor = cursor

    async def reconcile_batch(self, batch_id: UUID) -> None:
        """Recompute terminal statistics after downstream stages finish."""

        async with transaction(self._session_factory) as session:
            batch = await session.scalar(
                select(MailSyncBatch).where(MailSyncBatch.id == batch_id).with_for_update()
            )
            if batch is None:
                raise MailSyncError("BATCH_NOT_FOUND", "同步批次不存在")
            config = await session.scalar(
                select(MailboxConfig)
                .where(MailboxConfig.id == batch.mailboxConfigId)
                .with_for_update()
            )
            if config is None:
                raise MailSyncError("MAILBOX_NOT_FOUND", "邮箱配置不存在")
            await self._refresh_batch(session, batch, config)

    async def retry(self, handoff_id: UUID, operator_user_id: UUID | None = None) -> UUID:
        async with transaction(self._session_factory) as session:
            handoff = await session.scalar(
                select(MailSourceHandoff)
                .where(MailSourceHandoff.id == handoff_id)
                .with_for_update()
            )
            if handoff is None:
                raise MailSyncError("HANDOFF_NOT_FOUND", "邮件交接记录不存在")
            if handoff.parseStatus not in {
                MailStageStatus.RETRYABLE_FAILURE,
                MailStageStatus.PERMANENT_FAILURE,
            }:
                raise MailSyncError("HANDOFF_NOT_RETRYABLE", "当前邮件不支持重试")
            task = await enqueue_task(
                session,
                DurableTaskKind.MAIL_MESSAGE_RETRY,
                f"mail-retry:{handoff.mailboxConfigId}:{handoff.uidValidity}:{handoff.imapUid}:{uuid4()}",
                {
                    "mailbox_config_id": str(handoff.mailboxConfigId),
                    "uid_validity": handoff.uidValidity,
                    "imap_uid": handoff.imapUid,
                },
            )
            handoff.parseStatus = MailStageStatus.PENDING
            handoff.failureCode = None
            handoff.failureSummary = None
            return task.id

    async def _discover(self, config: MailboxConfig) -> MailSyncSnapshot:
        try:
            auth = self._cipher.decrypt_legacy(
                LegacySecretFields(
                    config.encryptedAuthCode, config.authCodeIv, config.authCodeTag, "v1"
                )
            )
        except SecretCryptoError:
            raise MailSyncError("SECRET_DECRYPTION_FAILED", "邮箱授权码解密失败") from None
        return await self._connection.discover(
            email=config.email,
            auth_code=auth,
            host=config.imapHost,
            port=config.imapPort,
            encryption=config.encryption.value,
            folder=config.folder,
            cursor=config.uidCursor,
            initial_sync_weeks=config.initialSyncWeeks,
        )

    @staticmethod
    def _candidate_filter(config: MailboxConfig) -> MailCandidateFilter:
        """Build the deterministic envelope candidate filter from a mailbox config."""

        raw_keywords = config.subjectKeywords
        keywords = (
            tuple(item for item in raw_keywords if isinstance(item, str) and item.strip())
            if isinstance(raw_keywords, list) and raw_keywords
            else DEFAULT_WEEKLY_REPORT_KEYWORDS
        )
        raw_allowlist = config.senderAllowlist
        allowlist = (
            tuple(item for item in raw_allowlist if isinstance(item, str) and item.strip())
            if isinstance(raw_allowlist, list)
            else ()
        )
        return MailCandidateFilter(
            MailCandidateFilterConfig(
                weekly_report_only=bool(config.weeklyReportOnly),
                subject_keywords=keywords,
                sender_allowlist=allowlist,
            )
        )

    @staticmethod
    async def _reclassify_historical(
        session: AsyncSession,
        config: MailboxConfig,
        candidate_filter: MailCandidateFilter,
    ) -> None:
        """Re-judge historical messages; hide non-weekly, risk-free ones.

        Already-synced ``MailMessage`` rows are re-evaluated against the
        candidate filter. Any row that would now be filtered out as a
        non-weekly-report and that never produced a risk candidate is marked
        ``SKIPPED`` + ``FILTERED`` so it disappears from the default list.
        Rows with a formal risk candidate (pending or otherwise) are never
        touched — the historical risk trail must survive rule changes. This is
        a safe, non-destructive cleanup: no message body is re-fetched and no
        row is physically deleted. Once a row is marked ``FILTERED`` it is not
        re-evaluated on subsequent syncs.
        """

        rows = (
            await session.scalars(
                select(MailMessage).where(
                    MailMessage.mailboxConfigId == config.id,
                    # Plain inequality, not is_not(): SQLAlchemy renders
                    # ``is_not(<non-null bind>)`` as ``IS NOT $1``, which
                    # PostgreSQL rejects as a syntax error.
                    MailMessage.skipReason != MailMessageSkipReason.FILTERED,
                    MailMessage.status != MailMessageStatus.FAILED,
                )
            )
        ).all()
        if not rows:
            return
        candidate_ids = set(
            await session.scalars(
                select(MailRiskCandidate.messageId).where(
                    MailRiskCandidate.messageId.in_([row.id for row in rows])
                )
            )
        )
        for row in rows:
            if row.id in candidate_ids:
                continue
            if candidate_filter.evaluate(subject=row.subject, sender=row.senderAddress):
                continue
            row.status = MailMessageStatus.SKIPPED
            row.skipReason = MailMessageSkipReason.FILTERED

    async def _handoff(
        self,
        session: AsyncSession,
        batch: MailSyncBatch,
        config: MailboxConfig,
        envelope: MailEnvelope,
    ) -> None:
        existing = await session.scalar(
            select(MailSourceHandoff)
            .where(
                MailSourceHandoff.mailboxConfigId == config.id,
                MailSourceHandoff.uidValidity == envelope.uid_validity,
                MailSourceHandoff.imapUid == envelope.uid,
            )
            .with_for_update()
        )
        if existing is not None:
            return
        task = await enqueue_task(
            session,
            DurableTaskKind.ATTACHMENT_PARSE,
            f"mail-parse:{config.id}:{envelope.uid_validity}:{envelope.uid}",
            {
                "mailbox_config_id": str(config.id),
                "uid_validity": envelope.uid_validity,
                "imap_uid": envelope.uid,
            },
        )
        received_at = envelope.received_at
        received_at_source = MailReceivedAtSource.IMAP_INTERNALDATE
        if received_at is None:
            received_at = await session.scalar(
                select(func.date_trunc("milliseconds", func.current_timestamp()))
            )
            if received_at is None:
                raise RuntimeError("PostgreSQL transaction timestamp unavailable")
            received_at_source = MailReceivedAtSource.FIRST_DURABLE_OBSERVATION
        session.add(
            MailSourceHandoff(
                mailboxConfigId=config.id,
                batchId=batch.id,
                parseTaskId=task.id,
                uidValidity=envelope.uid_validity,
                imapUid=envelope.uid,
                messageId=envelope.message_id,
                sentAt=envelope.sent_at,
                receivedAt=received_at,
                receivedAtSource=received_at_source,
                envelopeMetadata=_metadata(envelope),
            )
        )

    async def _refresh_batch(
        self, session: AsyncSession, batch: MailSyncBatch, config: MailboxConfig
    ) -> None:
        rows = (
            await session.scalars(
                select(MailSourceHandoff).where(MailSourceHandoff.batchId == batch.id)
            )
        ).all()
        batch.handedOffCount = len(rows)
        batch.scannedCount = batch.discoveredCount
        batch.newCount = batch.handedOffCount
        batch.downstreamPendingCount = sum(
            1
            for row in rows
            if row.parseStatus not in TERMINAL or row.aiReviewStatus not in TERMINAL
        )
        batch.retryableFailedCount = sum(
            1
            for row in rows
            if MailStageStatus.RETRYABLE_FAILURE
            in {row.fetchStatus, row.handoffStatus, row.parseStatus, row.aiReviewStatus}
        )
        batch.permanentlyFailedCount = sum(
            1
            for row in rows
            if any(
                status == MailStageStatus.PERMANENT_FAILURE
                for status in (
                    row.fetchStatus,
                    row.handoffStatus,
                    row.parseStatus,
                    row.aiReviewStatus,
                )
            )
        )
        batch.successCount = sum(
            1
            for row in rows
            if all(
                status == MailStageStatus.SUCCEEDED
                for status in (
                    row.fetchStatus,
                    row.handoffStatus,
                    row.parseStatus,
                    row.aiReviewStatus,
                )
            )
        )
        batch.failedCount = batch.retryableFailedCount + batch.permanentlyFailedCount
        # The scanned cursor advances to the highest *scanned* UID, not the
        # highest accepted weekly-report UID. ``endUid`` records the scan
        # high-water mark set during discover; once downstream stages have
        # settled (or there were no candidates at all) the durable
        # ``uidCursor`` advances past every scanned message so non-weekly mail
        # is never re-scanned. See mailbox candidate-filter ADR.
        highest_scanned = batch.endUid
        if highest_scanned is not None and (
            not rows
            or (batch.downstreamPendingCount == 0 and batch.retryableFailedCount == 0)
        ):
            config.uidCursor = max(config.uidCursor or 0, highest_scanned)
            batch.cursorAdvanced = True
        if not rows:
            # No candidates handed off (all scanned mail was filtered out as
            # non-weekly, or the folder was empty). The batch still succeeds
            # so the schedule does not retry an already-fully-scanned window.
            batch.status = MailSyncStatus.SUCCESS
        elif batch.downstreamPendingCount == 0 and batch.retryableFailedCount == 0:
            batch.status = (
                MailSyncStatus.PARTIAL if batch.permanentlyFailedCount else MailSyncStatus.SUCCESS
            )
        else:
            batch.status = MailSyncStatus.PARTIAL
            batch.errorSummary = "存在待下游处理或可重试邮件, UID cursor 未推进"
        batch.finishedAt = datetime.now(UTC)

    async def _fail_batch(self, batch_id: UUID, error: MailSyncError) -> None:
        async with transaction(self._session_factory) as session:
            batch = await session.scalar(
                select(MailSyncBatch).where(MailSyncBatch.id == batch_id).with_for_update()
            )
            if batch is not None:
                batch.status = MailSyncStatus.FAILURE
                batch.finishedAt = datetime.now(UTC)
                batch.errorSummary = error.summary
                batch.failedCount += 1


def _metadata(envelope: MailEnvelope) -> dict[str, object]:
    return {
        "subject": (envelope.subject or "")[:500],
        "sender": (envelope.sender or "")[:255],
        "sent_at": envelope.sent_at.isoformat() if envelope.sent_at else None,
    }


async def schedule_enabled_syncs(session_factory: async_sessionmaker[AsyncSession]) -> list[UUID]:
    """Create scheduled batches using the same PostgreSQL task/outbox facts."""

    ids: list[UUID] = []
    async with transaction(session_factory) as session:
        configs = (
            await session.scalars(
                select(MailboxConfig).where(
                    MailboxConfig.enabled.is_(True), MailboxConfig.autoSyncEnabled.is_(True)
                )
            )
        ).all()
        for config in configs:
            active = await session.scalar(
                select(MailSyncBatch.id).where(
                    MailSyncBatch.mailboxConfigId == config.id,
                    MailSyncBatch.status.in_([MailSyncStatus.QUEUED, MailSyncStatus.RUNNING]),
                )
            )
            if active is not None:
                continue
            code = f"MAIL-SCHEDULED-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
            task = await enqueue_task(
                session,
                DurableTaskKind.MAILBOX_SYNC,
                f"mailbox:{config.id}:scheduled:{code}",
                {"mailbox_config_id": str(config.id)},
            )
            batch = MailSyncBatch(
                taskId=task.id,
                code=code,
                mailboxConfigId=config.id,
                trigger=MailSyncTrigger.SCHEDULED,
            )
            session.add(batch)
            await session.flush()
            ids.append(batch.id)
    return ids


def sync_handler(service: MailboxSyncService) -> Callable[[Mapping[str, object]], Awaitable[None]]:
    """Adapter for the shared JSON-only durable task executor."""

    async def handle(payload: Mapping[str, object]) -> None:
        await service.run_for_config(UUID(str(payload["mailbox_config_id"])))

    return handle


__all__ = ["MailSyncError", "MailboxSyncService", "schedule_enabled_syncs"]
