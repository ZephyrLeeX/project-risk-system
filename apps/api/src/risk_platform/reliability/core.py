"""PostgreSQL is the source of truth for Celery work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID, uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.model_types import JSONValue, utc_now
from risk_platform.reliability.models import (
    DurableTask,
    DurableTaskKind,
    DurableTaskStatus,
    TaskOutbox,
)
from risk_platform.reliability.registry import TaskDefinition, task_definition

TaskHandler = Callable[[Mapping[str, JSONValue]], Awaitable[None]]
FINAL_STATUSES: Final[frozenset[DurableTaskStatus]] = frozenset(
    {DurableTaskStatus.SUCCEEDED, DurableTaskStatus.FAILED, DurableTaskStatus.CANCELLED}
)


class TaskConflict(RuntimeError):
    """A stale state or lease attempted a fenced update."""


class UnknownTask(RuntimeError):
    """A broker message referenced a task that is not present in PostgreSQL."""


async def create_task(
    session: AsyncSession,
    kind: DurableTaskKind,
    idempotency_key: str,
    payload: dict[str, JSONValue],
    *,
    definition: TaskDefinition | None = None,
) -> DurableTask:
    """Create or return an idempotent task. Caller owns the transaction."""

    if not idempotency_key.strip():
        raise ValueError("idempotency_key must not be blank")
    definition = definition or task_definition(kind)
    existing = await session.scalar(
        select(DurableTask).where(
            DurableTask.kind == kind, DurableTask.idempotencyKey == idempotency_key
        )
    )
    if existing is not None:
        return existing
    task = DurableTask(
        kind=kind,
        idempotencyKey=idempotency_key,
        payload=payload,
        maxAttempts=definition.max_attempts,
    )
    session.add(task)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        existing = cast(
            DurableTask | None,
            await session.scalar(
                select(DurableTask).where(
                    DurableTask.kind == kind, DurableTask.idempotencyKey == idempotency_key
                )
            ),
        )
        if existing is None:
            raise
        return existing
    return task


async def enqueue_task(
    session: AsyncSession,
    kind: DurableTaskKind,
    idempotency_key: str,
    payload: dict[str, JSONValue],
    *,
    definition: TaskDefinition | None = None,
) -> DurableTask:
    """Create a task and its first outbox fact in the same transaction."""

    task = await create_task(session, kind, idempotency_key, payload, definition=definition)
    if task.dispatchGeneration == 0:
        task.dispatchGeneration = 1
        session.add(TaskOutbox(taskId=task.id, dispatchGeneration=1))
        await session.flush()
    return task


async def claim_task(
    session: AsyncSession,
    task_id: UUID,
    dispatch_generation: int,
    owner: str,
    *,
    lease_seconds: int = 300,
) -> UUID | None:
    """Claim exactly one queued generation using an atomic fenced update."""

    now = utc_now()
    token = uuid4()
    result = await session.execute(
        update(DurableTask)
        .where(
            DurableTask.id == task_id,
            DurableTask.status == DurableTaskStatus.QUEUED,
            DurableTask.dispatchGeneration == dispatch_generation,
        )
        .values(
            status=DurableTaskStatus.RUNNING,
            leaseToken=token,
            leaseOwner=owner,
            heartbeatAt=now,
            leaseExpiresAt=now + timedelta(seconds=lease_seconds),
            attemptCount=DurableTask.attemptCount + 1,
            startedAt=now,
            updatedAt=now,
        )
    )
    if cast(CursorResult[object], result).rowcount != 1:
        return None
    return token


async def heartbeat(
    session: AsyncSession, task_id: UUID, lease_token: UUID, *, lease_seconds: int = 300
) -> bool:
    now = utc_now()
    result = await session.execute(
        update(DurableTask)
        .where(
            DurableTask.id == task_id,
            DurableTask.status == DurableTaskStatus.RUNNING,
            DurableTask.leaseToken == lease_token,
        )
        .values(
            heartbeatAt=now, leaseExpiresAt=now + timedelta(seconds=lease_seconds), updatedAt=now
        )
    )
    return cast(CursorResult[object], result).rowcount == 1


async def finish_task(
    session: AsyncSession,
    task_id: UUID,
    lease_token: UUID,
    *,
    success: bool,
    failure_code: str | None = None,
    failure_summary: str | None = None,
    retry_at: datetime | None = None,
    cancelled: bool = False,
) -> DurableTaskStatus:
    """Complete or schedule a fenced attempt; never trusts a stale worker."""

    task = await session.scalar(select(DurableTask).where(DurableTask.id == task_id))
    if task is None:
        raise UnknownTask(str(task_id))
    if task.status != DurableTaskStatus.RUNNING or task.leaseToken != lease_token:
        raise TaskConflict("task lease is stale")
    now = utc_now()
    if cancelled:
        status = DurableTaskStatus.CANCELLED
        values = dict(
            status=status,
            completedAt=now,
            nextRetryAt=None,
            failureCode="AGENT_EXECUTION_CANCELLED",
            failureSummary="execution cancelled at a safe provider boundary",
            updatedAt=now,
        )
    elif success:
        status = DurableTaskStatus.SUCCEEDED
        values = dict(
            status=status,
            completedAt=now,
            nextRetryAt=None,
            failureCode=None,
            failureSummary=None,
            updatedAt=now,
        )
    elif retry_at is not None and task.attemptCount < task.maxAttempts:
        status = DurableTaskStatus.RETRY_WAIT
        values = dict(
            status=status,
            completedAt=None,
            nextRetryAt=retry_at,
            failureCode=failure_code,
            failureSummary=failure_summary,
            updatedAt=now,
        )
    else:
        status = DurableTaskStatus.FAILED
        values = dict(
            status=status,
            completedAt=now,
            nextRetryAt=None,
            failureCode=failure_code,
            failureSummary=failure_summary,
            updatedAt=now,
        )
    result = await session.execute(
        update(DurableTask)
        .where(
            DurableTask.id == task_id,
            DurableTask.status == DurableTaskStatus.RUNNING,
            DurableTask.leaseToken == lease_token,
        )
        .values(**values, leaseToken=None, leaseOwner=None, heartbeatAt=None, leaseExpiresAt=None)
    )
    if cast(CursorResult[object], result).rowcount != 1:
        raise TaskConflict("task lease is stale")
    return status

async def reconcile(session: AsyncSession, *, now: datetime | None = None, limit: int = 100) -> int:
    """Rebuild dispatch facts and recover expired attempts from PostgreSQL alone."""

    # T025 task directories are never recovery state; periodic reconciliation removes stale ones.
    from risk_platform.mailbox.parsing import cleanup_stale_temp_directories

    cleanup_stale_temp_directories()

    current = now or datetime.now(UTC)
    recovered = 0
    query: Select[tuple[DurableTask]] = (
        select(DurableTask)
        .where(
            (
                (DurableTask.status == DurableTaskStatus.RETRY_WAIT)
                & (DurableTask.nextRetryAt <= current)
            )
            | (
                (DurableTask.status == DurableTaskStatus.RUNNING)
                & (DurableTask.leaseExpiresAt <= current)
            )
            | (
                (DurableTask.status == DurableTaskStatus.QUEUED)
                & (DurableTask.dispatchGeneration == 0)
            )
        )
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    for task in (await session.scalars(query)).all():
        if task.status == DurableTaskStatus.QUEUED:
            task.dispatchGeneration = 1
            session.add(TaskOutbox(taskId=task.id, dispatchGeneration=1))
        elif task.status == DurableTaskStatus.RUNNING:
            task.leaseToken = task.leaseOwner = task.heartbeatAt = task.leaseExpiresAt = None
            if task.attemptCount >= task.maxAttempts:
                task.status = DurableTaskStatus.FAILED
                task.completedAt = current
            else:
                task.status = DurableTaskStatus.RETRY_WAIT
                task.nextRetryAt = current
        else:
            task.status = DurableTaskStatus.QUEUED
            task.nextRetryAt = None
            task.dispatchGeneration += 1
            session.add(TaskOutbox(taskId=task.id, dispatchGeneration=task.dispatchGeneration))
        task.updatedAt = current
        recovered += 1
    return recovered


__all__ = [
    "FINAL_STATUSES",
    "TaskConflict",
    "UnknownTask",
    "claim_task",
    "create_task",
    "enqueue_task",
    "finish_task",
    "heartbeat",
    "reconcile",
]
