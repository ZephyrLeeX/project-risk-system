"""Bounded, auditable retention cleanup workers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.agent.models import AgentConfirmationToken, AgentConversation, AgentEvent
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.db import transaction
from risk_platform.imports.models import ImportBatch
from risk_platform.model_types import JSONValue
from risk_platform.reliability.models import DurableTask, DurableTaskStatus
from risk_platform.retention.configuration import require_utc
from risk_platform.retention.models import RetentionResourceType
from risk_platform.retention.service import (
    RetentionConfigurationRepository,
    RetentionDecision,
    RetentionHoldService,
    RetentionProtectionService,
)

_ACTIVE_TASK_STATUSES = (
    DurableTaskStatus.QUEUED,
    DurableTaskStatus.RUNNING,
    DurableTaskStatus.RETRY_WAIT,
)
_DELETED_STORAGE_PREFIX = "retention-deleted:"
_COMPLETE_STORAGE_PREFIX = "retention-complete:"
_DELETE_MARKER = ".retention-delete"
_TEMP_PREFIX = "risk-mail-"
_TEMP_MAX_AGE = timedelta(hours=1)


class CleanupFailure(RuntimeError):
    """A retryable cleanup failure with a fixed metadata-only code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CleanupItem:
    resource_type: str
    resource_id: str
    outcome: str
    reason: str | None = None


@dataclass(slots=True)
class CleanupReport:
    as_of: datetime
    dry_run: bool
    items: list[CleanupItem] = field(default_factory=list)
    temp_deleted: int = 0
    temp_failed: int = 0

    @property
    def failed(self) -> bool:
        return self.temp_failed > 0 or any(item.outcome == "FAILED" for item in self.items)


class ImportSourceCleaner:
    """Delete only the canonical source for one UUID batch below an explicit root."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("import storage root must be absolute")
        self._root = root.resolve()
        if self._root == Path(self._root.anchor):
            raise ValueError("import storage root must not be a filesystem root")

    def prepare_delete(self, batch_id: UUID, storage_key: str) -> Path | None:
        tombstone = self.tombstone(batch_id)
        batch_dir, source, marker = self._paths(batch_id)
        if storage_key == tombstone:
            return marker if marker.exists() else None
        if storage_key != f"{batch_id}/source.xlsx":
            raise CleanupFailure("RETENTION_STORAGE_TARGET_UNSAFE")
        batch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker_existed = marker.exists()
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        if source.exists():
            if not source.is_file() or source.is_symlink():
                raise CleanupFailure("RETENTION_STORAGE_TARGET_UNSAFE")
            source.unlink()
        elif not marker_existed:
            marker.unlink(missing_ok=True)
            with suppress(OSError):
                batch_dir.rmdir()
            raise CleanupFailure("RETENTION_SOURCE_MISSING")
        return marker

    def finish_delete(self, marker: Path | None) -> None:
        if marker is None:
            return
        try:
            entries = list(marker.parent.iterdir())
            if entries != [marker]:
                raise CleanupFailure("RETENTION_STORAGE_RESIDUE_PRESENT")
            marker.unlink(missing_ok=True)
            marker.parent.rmdir()
        except CleanupFailure:
            raise
        except OSError as error:
            with suppress(OSError):
                descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
            raise CleanupFailure("RETENTION_STORAGE_CLEANUP_FAILED") from error

    def finish_pending(self, batch_id: UUID) -> None:
        """Converge every marker-missing crash state without accepting residue."""

        batch_dir, _, marker = self._paths(batch_id)
        if not batch_dir.exists():
            return
        if not batch_dir.is_dir():
            raise CleanupFailure("RETENTION_STORAGE_TARGET_UNSAFE")
        if marker.is_file() and not marker.is_symlink():
            self.finish_delete(marker)
            return
        try:
            if any(batch_dir.iterdir()):
                self._ensure_marker(marker)
                raise CleanupFailure("RETENTION_STORAGE_RESIDUE_PRESENT")
            batch_dir.rmdir()
        except CleanupFailure:
            raise
        except OSError as error:
            self._ensure_marker(marker)
            raise CleanupFailure("RETENTION_STORAGE_CLEANUP_FAILED") from error

    def pending_marker(self, batch_id: UUID) -> Path | None:
        """Return a safe leftover marker without following a batch-directory symlink."""

        _, _, marker = self._paths(batch_id)
        return marker if marker.is_file() and not marker.is_symlink() else None

    @staticmethod
    def _ensure_marker(marker: Path) -> None:
        with suppress(FileExistsError):
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)

    @staticmethod
    def tombstone(batch_id: UUID) -> str:
        return f"{_DELETED_STORAGE_PREFIX}{batch_id}"

    @staticmethod
    def complete(batch_id: UUID) -> str:
        return f"{_COMPLETE_STORAGE_PREFIX}{batch_id}"

    def _paths(self, batch_id: UUID) -> tuple[Path, Path, Path]:
        batch_dir = self._root / str(batch_id)
        if batch_dir.is_symlink():
            raise CleanupFailure("RETENTION_STORAGE_TARGET_UNSAFE")
        resolved_parent = batch_dir.parent.resolve()
        if resolved_parent != self._root:
            raise CleanupFailure("RETENTION_STORAGE_TARGET_UNSAFE")
        return batch_dir, batch_dir / "source.xlsx", batch_dir / _DELETE_MARKER


class OrphanTempCleaner:
    """Remove only stale T025 directories directly below an explicit temp root."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("temporary storage root must be absolute")
        self._root = root.resolve()
        if self._root == Path(self._root.anchor):
            raise ValueError("temporary storage root must not be a filesystem root")

    def cleanup(self, *, as_of: datetime) -> tuple[int, int]:
        require_utc(as_of, field="as_of")
        deleted = failed = 0
        cutoff = as_of - _TEMP_MAX_AGE
        if not self._root.exists():
            return deleted, failed
        for path in self._root.glob(f"{_TEMP_PREFIX}*"):
            try:
                resolved = path.resolve()
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                if (
                    self._root not in resolved.parents
                    or resolved.parent != self._root
                    or not path.is_dir()
                    or path.is_symlink()
                    or modified >= cutoff
                ):
                    continue
                self._remove_tree(path)
                deleted += 1
            except OSError:
                failed += 1
        return deleted, failed

    @staticmethod
    def _remove_tree(root: Path) -> None:
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        root.rmdir()


class RetentionCleanupService:
    """Process a bounded cleanup batch; failed items remain retryable."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        import_storage_root: Path,
        temp_storage_root: Path,
        candidate_limit: int = 100,
    ) -> None:
        if candidate_limit < 1 or candidate_limit > 1000:
            raise ValueError("candidate_limit must be between 1 and 1000")
        self._session_factory = session_factory
        self._sources = ImportSourceCleaner(import_storage_root)
        self._temp = OrphanTempCleaner(temp_storage_root)
        self._holds = RetentionHoldService(session_factory)
        self._candidate_limit = candidate_limit

    async def run(self, *, as_of: datetime, trace_id: UUID, dry_run: bool) -> CleanupReport:
        require_utc(as_of, field="as_of")
        report = CleanupReport(as_of=as_of, dry_run=dry_run)
        batch_ids, conversation_ids = await self._candidates(as_of)
        for batch_id in batch_ids:
            report.items.append(
                await self._cleanup_import(
                    batch_id, as_of=as_of, trace_id=trace_id, dry_run=dry_run
                )
            )
        for conversation_id in conversation_ids:
            report.items.append(
                await self._cleanup_conversation(
                    conversation_id, as_of=as_of, trace_id=trace_id, dry_run=dry_run
                )
            )
        if not dry_run:
            report.temp_deleted, report.temp_failed = self._temp.cleanup(as_of=as_of)
            await self._audit_temp_result(report, trace_id)
        return report

    async def handle(self, payload: Mapping[str, JSONValue]) -> None:
        try:
            as_of = datetime.fromisoformat(str(payload["as_of"]).replace("Z", "+00:00"))
            trace_id = UUID(str(payload["trace_id"]))
            dry_run = payload.get("dry_run") is True
        except (KeyError, TypeError, ValueError) as error:
            raise CleanupFailure("RETENTION_PAYLOAD_INVALID") from error
        report = await self.run(as_of=as_of, trace_id=trace_id, dry_run=dry_run)
        if report.failed:
            raise CleanupFailure("RETENTION_CLEANUP_PARTIAL_FAILURE")

    async def _candidates(self, as_of: datetime) -> tuple[list[UUID], list[UUID]]:
        async with self._session_factory() as session:
            batches = list(
                (
                    await session.scalars(
                        select(ImportBatch.id)
                        .where(
                            ImportBatch.sourceExpiresAt <= as_of,
                            ~ImportBatch.storageKey.startswith(_DELETED_STORAGE_PREFIX),
                            ~ImportBatch.storageKey.startswith(_COMPLETE_STORAGE_PREFIX),
                        )
                        .order_by(ImportBatch.sourceExpiresAt, ImportBatch.id)
                        .limit(self._candidate_limit)
                    )
                ).all()
            )
            recoveries = list(
                (
                    await session.scalars(
                        select(ImportBatch.id)
                        .where(ImportBatch.storageKey.startswith(_DELETED_STORAGE_PREFIX))
                        .order_by(ImportBatch.id)
                        .limit(self._candidate_limit)
                    )
                ).all()
            )
            conversations = list(
                (
                    await session.scalars(
                        select(AgentConversation.id)
                        .where(AgentConversation.expiresAt <= as_of)
                        .order_by(AgentConversation.expiresAt, AgentConversation.id)
                        .limit(self._candidate_limit)
                    )
                ).all()
            )
        # Recovery and normal expiry each receive an independent bounded share.
        actionable_batches = list(dict.fromkeys([*recoveries, *batches]))
        return actionable_batches, conversations

    async def _cleanup_import(
        self, batch_id: UUID, *, as_of: datetime, trace_id: UUID, dry_run: bool
    ) -> CleanupItem:
        resource_id = str(batch_id)
        marker: Path | None = None
        try:
            async with transaction(self._session_factory) as session:
                await self._holds._lock_resource(
                    session, RetentionResourceType.IMPORT_BATCH, resource_id
                )
                await self._holds._lock_resource_fact(
                    session, RetentionResourceType.IMPORT_BATCH, resource_id
                )
                batch = await session.scalar(
                    select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
                )
                if batch is None:
                    raise CleanupFailure("RETENTION_FACT_MISSING")
                if batch.storageKey == self._sources.tombstone(batch.id):
                    if dry_run:
                        return CleanupItem("IMPORT_BATCH", resource_id, "ELIGIBLE")
                    self._sources.finish_pending(batch.id)
                    batch.storageKey = self._sources.complete(batch.id)
                    return CleanupItem("IMPORT_BATCH", resource_id, "DELETED")
                await self._holds.expire_due_locked(
                    session,
                    resource_type=RetentionResourceType.IMPORT_BATCH,
                    resource_id=resource_id,
                    as_of=as_of,
                    trace_id=trace_id,
                )
                active_hold = await self._holds._active_hold(
                    session, RetentionResourceType.IMPORT_BATCH, resource_id
                )
                known = await RetentionConfigurationRepository(session).for_version(
                    batch.retentionConfigVersion
                )
                active_operation = await session.scalar(
                    select(
                        exists().where(
                            DurableTask.id == batch.taskId,
                            DurableTask.status.in_(_ACTIVE_TASK_STATUSES),
                        )
                    )
                )
                result = RetentionProtectionService.import_batch(
                    batch,
                    retention_config_known=known is not None,
                    has_active_hold=active_hold is not None,
                    active_operation=bool(active_operation),
                    as_of=as_of,
                )
                if not result.eligible:
                    await self._audit_skip(
                        session, "IMPORT_BATCH", resource_id, result.decision, trace_id
                    )
                    return CleanupItem(
                        "IMPORT_BATCH", resource_id, "SKIPPED", result.decision.value
                    )
                if dry_run:
                    return CleanupItem("IMPORT_BATCH", resource_id, "ELIGIBLE")
                marker = self._sources.prepare_delete(batch.id, batch.storageKey)
                batch.storageKey = self._sources.tombstone(batch.id)
                await self._audit_deleted(session, "IMPORT_BATCH", resource_id, trace_id)
            self._sources.finish_delete(marker)
            await self._mark_import_complete(batch_id)
            return CleanupItem("IMPORT_BATCH", resource_id, "DELETED")
        except Exception as error:
            code = (
                error.code
                if isinstance(error, CleanupFailure)
                else "RETENTION_CLEANUP_IO_FAILED"
            )
            await self._audit_failure("IMPORT_BATCH", resource_id, trace_id, code)
            return CleanupItem("IMPORT_BATCH", resource_id, "FAILED", code)

    async def _mark_import_complete(self, batch_id: UUID) -> None:
        resource_id = str(batch_id)
        async with transaction(self._session_factory) as session:
            await self._holds._lock_resource(
                session, RetentionResourceType.IMPORT_BATCH, resource_id
            )
            await self._holds._lock_resource_fact(
                session, RetentionResourceType.IMPORT_BATCH, resource_id
            )
            batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
            )
            if batch is None or batch.storageKey != self._sources.tombstone(batch_id):
                raise CleanupFailure("RETENTION_FACT_CHANGED")
            batch.storageKey = self._sources.complete(batch_id)

    async def _cleanup_conversation(
        self, conversation_id: UUID, *, as_of: datetime, trace_id: UUID, dry_run: bool
    ) -> CleanupItem:
        resource_id = str(conversation_id)
        try:
            async with transaction(self._session_factory) as session:
                await self._holds._lock_resource(
                    session, RetentionResourceType.AGENT_CONVERSATION, resource_id
                )
                await self._holds._lock_resource_fact(
                    session, RetentionResourceType.AGENT_CONVERSATION, resource_id
                )
                conversation = await session.scalar(
                    select(AgentConversation)
                    .where(AgentConversation.id == conversation_id)
                    .with_for_update()
                )
                if conversation is None:
                    raise CleanupFailure("RETENTION_FACT_MISSING")
                await self._holds.expire_due_locked(
                    session,
                    resource_type=RetentionResourceType.AGENT_CONVERSATION,
                    resource_id=resource_id,
                    as_of=as_of,
                    trace_id=trace_id,
                )
                active_hold = await self._holds._active_hold(
                    session, RetentionResourceType.AGENT_CONVERSATION, resource_id
                )
                known = await RetentionConfigurationRepository(session).for_version(
                    conversation.retentionConfigVersion
                )
                active_operation = await self._conversation_active_operation(
                    session, conversation_id, as_of
                )
                result = RetentionProtectionService.conversation(
                    expires_at=conversation.expiresAt,
                    retention_config_version=conversation.retentionConfigVersion,
                    retention_config_known=known is not None,
                    has_active_hold=active_hold is not None,
                    active_operation=active_operation,
                    as_of=as_of,
                )
                if not result.eligible:
                    await self._audit_skip(
                        session,
                        "AGENT_CONVERSATION",
                        resource_id,
                        result.decision,
                        trace_id,
                    )
                    return CleanupItem(
                        "AGENT_CONVERSATION", resource_id, "SKIPPED", result.decision.value
                    )
                if dry_run:
                    return CleanupItem("AGENT_CONVERSATION", resource_id, "ELIGIBLE")
                await session.delete(conversation)
                await self._audit_deleted(session, "AGENT_CONVERSATION", resource_id, trace_id)
            return CleanupItem("AGENT_CONVERSATION", resource_id, "DELETED")
        except Exception as error:
            code = (
                error.code
                if isinstance(error, CleanupFailure)
                else "RETENTION_CLEANUP_DB_FAILED"
            )
            await self._audit_failure("AGENT_CONVERSATION", resource_id, trace_id, code)
            return CleanupItem("AGENT_CONVERSATION", resource_id, "FAILED", code)

    @staticmethod
    async def _conversation_active_operation(
        session: AsyncSession, conversation_id: UUID, as_of: datetime
    ) -> bool:
        confirmation = await session.scalar(
            select(
                exists().where(
                    AgentConfirmationToken.conversationId == conversation_id,
                    AgentConfirmationToken.usedAt.is_(None),
                    AgentConfirmationToken.expiresAt > as_of,
                )
            )
        )
        if confirmation:
            return True
        task = await session.scalar(
            select(
                exists()
                .where(
                    AgentEvent.conversationId == conversation_id,
                    AgentEvent.taskId == DurableTask.id,
                    DurableTask.status.in_(_ACTIVE_TASK_STATUSES),
                )
                .correlate_except(AgentEvent, DurableTask)
            )
        )
        return bool(task)

    @staticmethod
    async def _audit_deleted(
        session: AsyncSession, resource_type: str, resource_id: str, trace_id: UUID
    ) -> None:
        await AuditService(session).record_success(
            actor_id=None,
            actor_type=AuditActorType.SYSTEM,
            module="RETENTION",
            action="RETENTION_ARTIFACT_DELETED",
            resource_type=resource_type,
            resource_id=resource_id,
            trace_id=trace_id,
        )

    @staticmethod
    async def _audit_skip(
        session: AsyncSession,
        resource_type: str,
        resource_id: str,
        decision: RetentionDecision,
        trace_id: UUID,
    ) -> None:
        del decision
        await AuditService(session).record_success(
            actor_id=None,
            actor_type=AuditActorType.SYSTEM,
            module="RETENTION",
            action="RETENTION_CLEANUP_SKIPPED_PROTECTED",
            resource_type=resource_type,
            resource_id=resource_id,
            trace_id=trace_id,
        )

    async def _audit_failure(
        self, resource_type: str, resource_id: str, trace_id: UUID, code: str
    ) -> None:
        async with transaction(self._session_factory) as session:
            await AuditService(session).record_failure(
                actor_id=None,
                actor_type=AuditActorType.SYSTEM,
                module="RETENTION",
                action="RETENTION_CLEANUP_FAILED",
                resource_type=resource_type,
                resource_id=resource_id,
                trace_id=trace_id,
                failure_code=code,
            )

    async def _audit_temp_result(self, report: CleanupReport, trace_id: UUID) -> None:
        if report.temp_deleted:
            async with transaction(self._session_factory) as session:
                await self._audit_deleted(
                    session, "TEMPORARY_ARTIFACT", "orphan-temp", trace_id
                )
        if report.temp_failed:
            await self._audit_failure(
                "TEMPORARY_ARTIFACT",
                "orphan-temp",
                trace_id,
                "RETENTION_TEMP_CLEANUP_FAILED",
            )


__all__ = [
    "CleanupFailure",
    "CleanupItem",
    "CleanupReport",
    "ImportSourceCleaner",
    "OrphanTempCleaner",
    "RetentionCleanupService",
]
