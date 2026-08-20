"""Concurrency primitives for same-content import policy."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_file_hash(session: AsyncSession, file_hash: str) -> None:
    """Serialize upload and confirmation decisions for one workbook hash."""

    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(file_hash, 0)))
    )


__all__ = ["lock_file_hash"]
