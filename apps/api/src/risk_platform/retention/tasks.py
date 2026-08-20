"""Retention durable-task creation and worker registration."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.reliability.core import TaskHandler, enqueue_task
from risk_platform.reliability.models import DurableTask, DurableTaskKind
from risk_platform.retention.cleanup import RetentionCleanupService
from risk_platform.retention.configuration import require_utc


async def enqueue_cleanup(
    session: AsyncSession,
    *,
    as_of: datetime,
    trace_id: UUID,
    dry_run: bool = False,
) -> DurableTask:
    """Create one idempotent cleanup run for a fixed UTC instant."""

    require_utc(as_of, field="as_of")
    instant = as_of.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    mode = "dry-run" if dry_run else "delete"
    return await enqueue_task(
        session,
        DurableTaskKind.RETENTION_CLEANUP,
        f"retention-cleanup:{mode}:{instant}",
        {
            "as_of": instant,
            "trace_id": str(trace_id),
            "dry_run": dry_run,
            "policy_contract": "ADR0027",
        },
    )


def handlers(service: RetentionCleanupService) -> dict[str, TaskHandler]:
    return {DurableTaskKind.RETENTION_CLEANUP.value: service.handle}


__all__ = ["enqueue_cleanup", "handlers"]
