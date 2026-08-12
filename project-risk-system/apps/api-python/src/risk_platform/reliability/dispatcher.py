"""Transactional-outbox publisher and worker execution boundary."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.model_types import utc_now
from risk_platform.reliability.core import TaskHandler, claim_task, finish_task
from risk_platform.reliability.models import DurableTask, TaskOutbox
from risk_platform.reliability.registry import task_definition


async def publish_outbox(session: AsyncSession, celery: Celery, *, limit: int = 100) -> int:
    """Publish after commit; marking published happens only after broker acceptance."""

    rows = (
        await session.scalars(
            select(TaskOutbox)
            .where(TaskOutbox.publishedAt.is_(None))
            .order_by(TaskOutbox.createdAt)
            .limit(limit)
        )
    ).all()
    published = 0
    for row in rows:
        celery.send_task(
            "risk_platform.reliability.execute",
            args=[str(row.taskId), row.dispatchGeneration],
        )
        row.publishedAt = utc_now()
        published += 1
    return published


async def execute_message(
    session_factory: async_sessionmaker[AsyncSession],
    celery: Celery,
    task_id: UUID,
    dispatch_generation: int,
    *,
    owner: str,
    handlers: Mapping[str, TaskHandler],
) -> None:
    """Execute only a DB-claimed task; duplicate messages become harmless no-ops."""

    async with session_factory() as session:
        async with session.begin():
            token = await claim_task(session, task_id, dispatch_generation, owner)
            if token is None:
                return
            task = await session.get(DurableTask, task_id)
            if task is None:
                return
            handler = handlers.get(task.kind.value)
            if handler is None:
                await finish_task(
                    session,
                    task_id,
                    token,
                    success=False,
                    failure_code="TASK_HANDLER_NOT_REGISTERED",
                    failure_summary="approved task kind has no registered worker handler",
                )
                return
        try:
            await handler(task.payload)
        except Exception as exc:
            async with session.begin():
                await finish_task(
                    session,
                    task_id,
                    token,
                    success=False,
                    failure_code="TASK_FAILED",
                    failure_summary=type(exc).__name__,
                    retry_at=datetime.now(UTC) + timedelta(
                        seconds=task_definition(task.kind).retry_backoff_seconds
                        * (2 ** max(task.attemptCount - 1, 0))
                    ),
                )
        else:
            async with session.begin():
                await finish_task(session, task_id, token, success=True)


def register_executor(
    celery: Celery,
    session_factory: async_sessionmaker[AsyncSession],
    handlers: Mapping[str, TaskHandler],
    *,
    owner: str,
) -> None:
    """Register the small JSON-only Celery entrypoint in a worker process."""

    @celery.task(  # type: ignore[untyped-decorator]
        name="risk_platform.reliability.execute", shared=False, lazy=False
    )
    def execute(task_id: str, dispatch_generation: int) -> None:
        import asyncio

        asyncio.run(
            execute_message(
                session_factory,
                celery,
                UUID(task_id),
                dispatch_generation,
                owner=owner,
                handlers=handlers,
            )
        )

    del execute


__all__ = ["execute_message", "publish_outbox", "register_executor"]
