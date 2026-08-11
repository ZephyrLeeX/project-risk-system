"""Application boundary for durable workbook preview creation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.imports.models import ImportBatch
from risk_platform.imports.parser import MAIN_SHEET, MAX_WORKBOOK_BYTES, WorkbookError
from risk_platform.imports.storage import WorkbookStorage
from risk_platform.model_types import new_uuid
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind


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
        import hashlib

        digest = hashlib.sha256(content).hexdigest()
        batch_id: UUID | None = None
        try:
            async with self._transaction() as session:
                existing = await session.scalar(
                    select(ImportBatch).where(ImportBatch.fileHash == digest)
                )
                if existing is not None:
                    return existing
                batch_id = new_uuid()
                key, _ = await self._storage.save(batch_id, safe_name, content)
                task = await enqueue_task(
                    session,
                    DurableTaskKind.IMPORT_PREVIEW,
                    f"IMPORT_PREVIEW:{digest}",
                    {"batch_id": str(batch_id), "storage_key": key},
                )
                batch = ImportBatch(
                    id=batch_id,
                    taskId=task.id,
                    fileName=safe_name,
                    fileHash=digest,
                    storageKey=key,
                    sheetName=MAIN_SHEET,
                    totalRows=0,
                    readyRows=0,
                    warningRows=0,
                    errorRows=0,
                    uploadedById=uploaded_by,
                )
                session.add(batch)
                return batch
        except BaseException:
            if batch_id is not None:
                self._storage.remove_batch(batch_id)
            raise


__all__ = ["ImportPreviewService"]
