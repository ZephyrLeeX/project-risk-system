"""T037 — audit chain and reliability acceptance.

Proves the cross-cutting durability invariants from Design §10/§11 and the
approved ADRs (0018 outbox, 0027 retention, 0031 backup) hold at release level:
the append-only audit hash chain stays valid after a real business write and
rejects every mutation; durable tasks are idempotent, recover from a crashed
worker on restart, redispatch orphaned generations, and bridge the
transactional outbox to Celery; and a rolled-back business write leaves no
partial risk/todo/timeline/audit facts behind.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from risk_platform.audit.models import AuditLog
from risk_platform.audit.service import AuditService
from risk_platform.db import transaction
from risk_platform.reliability.core import claim_task, create_task, enqueue_task, reconcile
from risk_platform.reliability.dispatcher import publish_outbox
from risk_platform.reliability.models import (
    DurableTask,
    DurableTaskKind,
    DurableTaskStatus,
    TaskOutbox,
)
from risk_platform.risks.models import ProjectRiskLevel, Risk, RiskSourceType
from risk_platform.risks.service import RiskCreate, RisksService
from risk_platform.todos.models import ActionItem

from .conftest import PROJECT_MANAGER_ID, AcceptanceHarness


def _risk_command(acceptance: AcceptanceHarness, *, marker: str) -> RiskCreate:
    return RiskCreate(
        project_id=acceptance.env.seed.projects["owned"],
        category_id=acceptance.env.seed.category_id,  # type: ignore[arg-type]
        title=f"验收风险-{marker}",
        description=f"{marker} 验收风险描述 用于审计与回滚原子性验证",
        level=ProjectRiskLevel.HIGH,
        source_type=RiskSourceType.MANUAL,
        dedupe_fingerprint=f"acceptance-{marker}",
        suggestion=f"回滚唯一待办标记-{marker}",
        actor_name="验收执行人",
    )


# --------------------------------------------------------------------------- #
# Audit chain: integrity after a business write + append-only immutability    #
# --------------------------------------------------------------------------- #


def test_business_write_appends_a_verifiable_audit_chain_link(
    acceptance: AcceptanceHarness,
) -> None:
    trace = uuid.uuid4()

    async def scenario() -> None:
        command = _risk_command(acceptance, marker=f"chain-{trace}")
        async with transaction(acceptance.env.factory) as session:
            await RisksService(acceptance.env.factory).create_in_session(
                session, command, actor_id=PROJECT_MANAGER_ID, trace_id=trace
            )
        async with transaction(acceptance.env.factory) as session:
            integrity = await AuditService(session).verify_integrity()
            written = await session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.traceId == str(trace))
            )
        assert integrity.status == "VALID"
        assert integrity.first_broken_event_id is None
        assert integrity.verified_records == integrity.total_records
        assert integrity.total_records >= 1
        assert written == 1

    asyncio.run(scenario())


def test_audit_log_is_append_only_and_rejects_every_mutation(
    acceptance: AcceptanceHarness,
) -> None:
    trace = uuid.uuid4()

    async def scenario() -> None:
        command = _risk_command(acceptance, marker=f"immutable-{trace}")
        async with transaction(acceptance.env.factory) as session:
            await RisksService(acceptance.env.factory).create_in_session(
                session, command, actor_id=PROJECT_MANAGER_ID, trace_id=trace
            )
        mutations = (
            "UPDATE \"audit_logs\" SET module = 'TAMPER'",
            'DELETE FROM "audit_logs" WHERE id IN (SELECT id FROM "audit_logs" LIMIT 1)',
            'TRUNCATE "audit_logs"',
        )
        for statement in mutations:
            with pytest.raises(DBAPIError):
                async with acceptance.env.factory() as session:
                    async with session.begin():
                        await session.execute(text(statement))

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# Reliability: idempotent enqueue, crashed-worker recovery, orphan redispatch #
# --------------------------------------------------------------------------- #


def test_durable_task_enqueue_is_idempotent(acceptance: AcceptanceHarness) -> None:
    key = f"acceptance-idempotency-{uuid.uuid4()}"

    async def scenario() -> None:
        async with transaction(acceptance.env.factory) as session:
            first = await enqueue_task(session, DurableTaskKind.IMPORT_PREVIEW, key, {"batch": "x"})
            second = await enqueue_task(
                session, DurableTaskKind.IMPORT_PREVIEW, key, {"batch": "x"}
            )
            outbox = await session.scalar(
                select(func.count()).select_from(TaskOutbox).where(TaskOutbox.taskId == first.id)
            )
        assert first.id == second.id
        assert first.status == DurableTaskStatus.QUEUED
        assert first.dispatchGeneration == 1
        assert outbox == 1

    asyncio.run(scenario())


def test_reconcile_recovers_a_crashed_worker_expired_lease(
    acceptance: AcceptanceHarness,
) -> None:
    key = f"acceptance-restart-{uuid.uuid4()}"

    async def scenario() -> None:
        async with transaction(acceptance.env.factory) as session:
            task = await enqueue_task(
                session, DurableTaskKind.MAILBOX_SYNC, key, {"mailbox": "inbound"}
            )
            token = await claim_task(session, task.id, 1, "crashed-worker", lease_seconds=300)
            assert token is not None
            # Simulate the worker dying: its lease is now in the past. Backdate
            # both heartbeat and expiry (expiry stays after heartbeat to honour
            # the durable_tasks_lease_expiry_after_heartbeat check constraint).
            expired = datetime.now(UTC) - timedelta(seconds=10)
            await session.execute(
                update(DurableTask)
                .where(DurableTask.id == task.id)
                .values(heartbeatAt=expired - timedelta(minutes=1), leaseExpiresAt=expired)
            )
            recovered = await reconcile(session)
        assert recovered >= 1
        async with transaction(acceptance.env.factory) as session:
            reloaded = await session.get(DurableTask, task.id)
        assert reloaded is not None
        assert reloaded.status == DurableTaskStatus.RETRY_WAIT
        assert reloaded.leaseToken is None
        assert reloaded.nextRetryAt is not None

    asyncio.run(scenario())


def test_reconcile_redispatches_orphaned_generation_zero_task(
    acceptance: AcceptanceHarness,
) -> None:
    key = f"acceptance-orphan-{uuid.uuid4()}"

    async def scenario() -> None:
        async with transaction(acceptance.env.factory) as session:
            task = await create_task(
                session, DurableTaskKind.RETENTION_CLEANUP, key, {"scope": "all"}
            )
            assert task.dispatchGeneration == 0
            outbox_before = await session.scalar(
                select(func.count()).select_from(TaskOutbox).where(TaskOutbox.taskId == task.id)
            )
            assert outbox_before == 0
            recovered = await reconcile(session)
        assert recovered >= 1
        async with transaction(acceptance.env.factory) as session:
            reloaded = await session.get(DurableTask, task.id)
            outbox_after = await session.scalar(
                select(func.count()).select_from(TaskOutbox).where(TaskOutbox.taskId == task.id)
            )
        assert reloaded is not None
        assert reloaded.dispatchGeneration == 1
        assert outbox_after == 1

    asyncio.run(scenario())


class _CeleryRecorder:
    """Minimal Celery stand-in recording every dispatched task."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def send_task(self, name: str, args: tuple[object, ...] | None = None, **_: object) -> None:
        self.calls.append((name, tuple(args or ())))


def test_outbox_publisher_dispatches_to_celery_and_marks_published(
    acceptance: AcceptanceHarness,
) -> None:
    key = f"acceptance-outbox-{uuid.uuid4()}"

    async def scenario() -> None:
        async with transaction(acceptance.env.factory) as session:
            task = await enqueue_task(
                session, DurableTaskKind.WEEKLY_REPORT_REBUILD, key, {"week": "2026-W33"}
            )
        recorder = _CeleryRecorder()
        async with transaction(acceptance.env.factory) as session:
            published = await publish_outbox(session, recorder, limit=50)
        assert published >= 1
        dispatched = [
            (name, args)
            for name, args in recorder.calls
            if name == "risk_platform.reliability.execute" and args and args[0] == str(task.id)
        ]
        assert dispatched, recorder.calls
        assert dispatched[0][1][1] == 1  # dispatch generation
        async with transaction(acceptance.env.factory) as session:
            row = await session.scalar(select(TaskOutbox).where(TaskOutbox.taskId == task.id))
        assert row is not None
        assert row.publishedAt is not None

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# Transaction atomicity: a rolled-back write leaves no partial facts           #
# --------------------------------------------------------------------------- #


def test_rolled_back_business_write_leaves_no_partial_facts(
    acceptance: AcceptanceHarness,
) -> None:
    trace = uuid.uuid4()

    async def scenario() -> None:
        command = _risk_command(acceptance, marker=f"rollback-{trace}")
        service = RisksService(acceptance.env.factory)
        with pytest.raises(RuntimeError, match="simulated downstream failure"):
            async with acceptance.env.factory() as session:
                async with session.begin():
                    await service.create_in_session(
                        session, command, actor_id=PROJECT_MANAGER_ID, trace_id=trace
                    )
                    raise RuntimeError("simulated downstream failure")
        async with transaction(acceptance.env.factory) as session:
            risk = await session.scalar(
                select(Risk).where(Risk.dedupeFingerprint == command.dedupe_fingerprint)
            )
            audit = await session.scalar(select(AuditLog).where(AuditLog.traceId == str(trace)))
            todo = await session.scalar(
                select(ActionItem).where(ActionItem.description == command.suggestion)
            )
        assert risk is None
        assert audit is None
        assert todo is None

    asyncio.run(scenario())
