"""Application boundary for durable workbook preview creation."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.imports.duplicate_policy import lock_file_hash
from risk_platform.imports.models import ImportBatch, ImportBatchStatus
from risk_platform.imports.parser import MAIN_SHEET, MAX_WORKBOOK_BYTES, WorkbookError
from risk_platform.imports.storage import WorkbookStorage
from risk_platform.model_types import new_uuid
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.retention.service import RetentionConfigurationRepository
from risk_platform.shared.errors import ApiError


class ImportPreviewService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], storage_root: Path
    ) -> None:
        self._session_factory = session_factory
        self._storage = WorkbookStorage(storage_root)

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session, session.begin():
            yield session

    async def create_preview(
        self, *, file_name: str, content: bytes, uploaded_by: UUID
    ) -> ImportBatch:
        safe_name = self._storage.validate(file_name, content)
        if len(content) > MAX_WORKBOOK_BYTES:
            raise WorkbookError("Excel 文件大小不能超过 20MB")
        digest = hashlib.sha256(content).hexdigest()
        batch_id: UUID | None = None
        try:
            async with self._transaction() as session:
                await lock_file_hash(session, digest)
                existing = await session.scalar(
                    select(ImportBatch)
                    .where(ImportBatch.fileHash == digest)
                    .order_by(ImportBatch.createdAt.desc(), ImportBatch.id.desc())
                    .with_for_update()
                )
                if existing is not None:
                    if existing.status in {
                        ImportBatchStatus.PROCESSING,
                        ImportBatchStatus.PREVIEWED,
                    }:
                        return existing
                    if existing.status is ImportBatchStatus.IMPORTED:
                        raise ApiError(
                            409,
                            "IMPORT_FILE_ALREADY_IMPORTED",
                            "相同内容的文件已经导入，请从导入历史查看",  # noqa: RUF001
                            data={"existingBatchId": str(existing.id)},
                        )
                    # FAILED and ROLLED_BACK start a new preview attempt. The
                    # terminal task record is intentionally never reused.
                batch_id = new_uuid()
                retention = await RetentionConfigurationRepository(session).current()
                created_at = datetime.now(UTC)
                key, _ = await self._storage.save(batch_id, safe_name, content)
                task = await enqueue_task(
                    session,
                    DurableTaskKind.IMPORT_PREVIEW,
                    f"IMPORT_PREVIEW:{batch_id}",
                    {"batch_id": str(batch_id), "storage_key": key},
                )
                batch = ImportBatch(
                    id=batch_id,
                    taskId=task.id,
                    fileName=safe_name,
                    fileHash=digest,
                    storageKey=key,
                    sheetName=MAIN_SHEET,
                    status=ImportBatchStatus.PROCESSING,
                    totalRows=0,
                    readyRows=0,
                    warningRows=0,
                    errorRows=0,
                    uploadedById=uploaded_by,
                    createdAt=created_at,
                    sourceExpiresAt=retention.source_expires_at(created_at),
                    retentionConfigVersion=retention.version,
                )
                session.add(batch)
                return batch
        except BaseException:
            if batch_id is not None:
                self._storage.remove_batch(batch_id)
            raise


__all__ = ["ImportPreviewService"]
