from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import redis as redis_lib
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.db import create_database_engine, create_session_factory, dispose_database_engine
from risk_platform.reliability.celery_app import create_celery_app
from risk_platform.reliability.models import TaskOutbox
from risk_platform.scheduler import (
    ADVISORY_LOCK_KEY,
    ActionSpec,
    Cadence,
    LivenessHTTPServer,
    LivenessState,
    Scheduler,
    SchedulerConfigurationError,
    acquire_advisory_lock,
    acquire_lock_with_retry,
    connect_with_retry,
    make_drain_outbox,
    make_mailbox_sync,
    make_reconcile,
    release_advisory_lock,
)

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class _Broker:
    """Minimal Celery stand-in recording send_task calls; optionally fails N times."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.messages: list[tuple[str, list[object]]] = []

    def send_task(self, name: str, *, args: list[object]) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("broker unavailable")
        self.messages.append((name, args))


async def _noop_action() -> None:
    return None


async def _acquire_true() -> bool:
    return True


async def _release_noop() -> None:
    return None


async def _ping_ok() -> None:
    return None


async def _sleep_noop(seconds: float) -> None:
    return None


# --------------------------------------------------------------------------- #
# Cadence configuration
# --------------------------------------------------------------------------- #


def test_cadence_defaults_match_adr_0030() -> None:
    cadence = Cadence()
    assert cadence.outbox_drain_seconds == 5.0
    assert cadence.reconcile_seconds == 30.0
    assert cadence.mailbox_sync_seconds == 300.0
    assert cadence.liveness_window_seconds == 10.0


def test_cadence_from_env_overrides() -> None:
    env = {
        "SCHEDULER_OUTBOX_DRAIN_INTERVAL_SECONDS": "7",
        "SCHEDULER_RECONCILE_INTERVAL_SECONDS": "40",
        "SCHEDULER_MAILBOX_SYNC_INTERVAL_SECONDS": "600",
        "SCHEDULER_POLL_INTERVAL_SECONDS": "2",
    }
    cadence = Cadence.from_env(env)
    assert cadence.outbox_drain_seconds == 7.0
    assert cadence.reconcile_seconds == 40.0
    assert cadence.mailbox_sync_seconds == 600.0
    assert cadence.poll_seconds == 2.0
    assert cadence.liveness_window_seconds == 14.0


def test_cadence_from_env_rejects_non_positive() -> None:
    with pytest.raises(SchedulerConfigurationError):
        Cadence.from_env({"SCHEDULER_OUTBOX_DRAIN_INTERVAL_SECONDS": "0"})
    with pytest.raises(SchedulerConfigurationError):
        Cadence.from_env({"SCHEDULER_RECONCILE_INTERVAL_SECONDS": "not-a-number"})


# --------------------------------------------------------------------------- #
# Single-active rejection (unit path) + lock retry helpers
# --------------------------------------------------------------------------- #


def test_second_instance_is_rejected_without_releasing() -> None:
    released: list[bool] = []

    async def release() -> None:
        released.append(True)

    liveness = LivenessState(10.0)
    scheduler = Scheduler(
        actions=[ActionSpec("x", _noop_action, 1.0)],
        cadence=Cadence(),
        acquire=_acquire_false,
        release=release,
        lock_ping=_ping_ok,
        liveness=liveness,
        clock=_FakeClock(0.0),
    )
    code = asyncio.run(scheduler.run(asyncio.Event()))
    assert code == 1
    assert released == []
    assert not liveness.healthy(0.0)


async def _acquire_false() -> bool:
    return False


class _FakeLockConn:
    def __init__(self, *, fail_times: int = 0, acquire_result: bool = True) -> None:
        self.fail_times = fail_times
        self.acquire_result = acquire_result
        self.executes = 0
        self.commits = 0

    async def execute(self, stmt: object, params: dict[str, object] | None = None) -> object:
        self.executes += 1
        if self.executes <= self.fail_times:
            raise OperationalError(
                "SELECT pg_try_advisory_lock", params or {}, RuntimeError("boom")
            )
        return SimpleNamespace(scalar=lambda: self.acquire_result)

    async def commit(self) -> None:
        self.commits += 1


def test_acquire_lock_with_retry_retries_on_dbapi_error_then_succeeds() -> None:
    conn = _FakeLockConn(fail_times=2)

    async def exercise() -> bool:
        return await acquire_lock_with_retry(
            conn,  # type: ignore[arg-type]
            ADVISORY_LOCK_KEY,
            retries=5,
            backoff=0.0,
            sleep=_sleep_noop,
        )

    acquired = asyncio.run(exercise())
    assert acquired is True
    assert conn.executes == 3
    assert conn.commits == 1


def test_acquire_lock_returns_false_immediately_when_held() -> None:
    conn = _FakeLockConn(fail_times=0, acquire_result=False)

    async def exercise() -> bool:
        return await acquire_lock_with_retry(
            conn,  # type: ignore[arg-type]
            ADVISORY_LOCK_KEY,
            retries=5,
            backoff=0.0,
            sleep=_sleep_noop,
        )

    acquired = asyncio.run(exercise())
    assert acquired is False
    assert conn.executes == 1  # fail-fast: a held lock is not retried


class _FakeEngine:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.connects = 0

    async def connect(self) -> str:
        self.connects += 1
        if self.connects <= self.fail_times:
            raise OperationalError("connect", {}, RuntimeError("boom"))
        return "connected"


def test_connect_with_retry_retries_then_succeeds() -> None:
    engine = _FakeEngine(fail_times=2)

    async def exercise() -> object:
        return await connect_with_retry(engine, retries=5, backoff=0.0, sleep=_sleep_noop)  # type: ignore[arg-type]

    result = asyncio.run(exercise())
    assert result == "connected"
    assert engine.connects == 3


# --------------------------------------------------------------------------- #
# Cadence scheduling + per-function invocation + failure isolation + retry
# --------------------------------------------------------------------------- #


def test_cadence_scheduling_drives_each_function_at_its_own_interval() -> None:
    clock = _FakeClock(0.0)
    calls: dict[str, list[float]] = defaultdict(list)

    def make(name: str, *, fail: bool = False) -> object:
        async def action() -> None:
            calls[name].append(clock.t)
            if fail:
                raise RuntimeError(f"{name} failed")

        return action

    cadence = Cadence(outbox_drain_seconds=5.0, reconcile_seconds=10.0, mailbox_sync_seconds=15.0)
    scheduler = Scheduler(
        actions=[
            ActionSpec("outbox", make("outbox"), 5.0),  # type: ignore[arg-type]
            ActionSpec("reconcile", make("reconcile"), 10.0),  # type: ignore[arg-type]
            ActionSpec("mailbox", make("mailbox"), 15.0),  # type: ignore[arg-type]
        ],
        cadence=cadence,
        acquire=_acquire_true,
        release=_release_noop,
        lock_ping=_ping_ok,
        liveness=LivenessState(cadence.liveness_window_seconds),
        clock=clock,
    )

    async def exercise() -> None:
        for t in range(0, 32):
            clock.t = float(t)
            await scheduler.tick()

    asyncio.run(exercise())
    assert calls["outbox"] == [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    assert calls["reconcile"] == [0.0, 10.0, 20.0, 30.0]
    assert calls["mailbox"] == [0.0, 15.0, 30.0]


def test_independent_failure_isolation_and_retry() -> None:
    clock = _FakeClock(0.0)
    calls: list[str] = []

    async def boom() -> None:
        calls.append("boom")
        raise RuntimeError("boom")

    async def ok1() -> None:
        calls.append("ok1")

    async def ok2() -> None:
        calls.append("ok2")

    scheduler = Scheduler(
        actions=[
            ActionSpec("boom", boom, 0.0),
            ActionSpec("ok1", ok1, 0.0),
            ActionSpec("ok2", ok2, 0.0),
        ],
        cadence=Cadence(),
        acquire=_acquire_true,
        release=_release_noop,
        lock_ping=_ping_ok,
        liveness=LivenessState(10.0),
        clock=clock,
    )

    async def exercise() -> None:
        await scheduler.tick()
        clock.t = 1.0
        await scheduler.tick()

    asyncio.run(exercise())
    # tick 1: boom fails but ok1/ok2 still run; tick 2: boom is retried (not advanced)
    assert calls == ["boom", "ok1", "ok2", "boom", "ok1", "ok2"]


# --------------------------------------------------------------------------- #
# Graceful shutdown
# --------------------------------------------------------------------------- #


def test_graceful_shutdown_completes_current_tick_and_releases() -> None:
    calls: list[str] = []
    stop = asyncio.Event()
    released: list[bool] = []

    async def a1() -> None:
        calls.append("a1")

    async def a2() -> None:
        calls.append("a2")
        stop.set()

    async def a3() -> None:
        calls.append("a3")

    async def release() -> None:
        released.append(True)

    scheduler = Scheduler(
        actions=[
            ActionSpec("a1", a1, 0.0),
            ActionSpec("a2", a2, 0.0),
            ActionSpec("a3", a3, 0.0),
        ],
        cadence=Cadence(),
        acquire=_acquire_true,
        release=release,
        lock_ping=_ping_ok,
        liveness=LivenessState(10.0),
        clock=_FakeClock(0.0),
        sleep=_sleep_noop,
    )

    async def exercise() -> int:
        return await scheduler.run(stop)

    code = asyncio.run(exercise())
    assert code == 0
    assert calls == ["a1", "a2", "a3"]  # current tick finished despite stop mid-tick
    assert released == [True]


def test_lock_connection_loss_stops_the_loop() -> None:
    released: list[bool] = []

    async def ping_fail() -> None:
        raise RuntimeError("lock connection lost")

    async def release() -> None:
        released.append(True)

    scheduler = Scheduler(
        actions=[ActionSpec("x", _noop_action, 1.0)],
        cadence=Cadence(),
        acquire=_acquire_true,
        release=release,
        lock_ping=ping_fail,
        liveness=LivenessState(10.0),
        clock=_FakeClock(0.0),
    )

    async def exercise() -> int:
        return await scheduler.run(asyncio.Event())

    code = asyncio.run(exercise())
    assert code == 1  # lock lost -> exit non-zero to trigger restart
    assert released == [True]


# --------------------------------------------------------------------------- #
# Liveness state + HTTP probe
# --------------------------------------------------------------------------- #


def test_liveness_state_reflects_lock_and_recent_tick() -> None:
    state = LivenessState(10.0)
    assert not state.healthy(0.0)
    state.set_lock_held(True)
    assert not state.healthy(0.0)  # no tick yet
    state.record_tick(5.0)
    assert state.healthy(8.0)  # within window
    assert not state.healthy(16.0)  # stale tick
    state.set_lock_held(False)
    assert not state.healthy(8.0)  # lock lost


def test_liveness_http_probe_reports_200_then_503() -> None:
    clock = _FakeClock(1.0)
    state = LivenessState(10.0)
    state.set_lock_held(True)
    state.record_tick(0.0)
    server = LivenessHTTPServer(state, "127.0.0.1", 0, clock=clock)
    port = server._httpd.server_address[1]
    server.start()
    try:

        def get() -> tuple[int, bytes]:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        assert get()[0] == 200  # healthy: lock held + recent tick
        clock.t = 100.0  # stale -> unhealthy
        assert get()[0] == 503
        state.set_lock_held(False)  # lock lost -> unhealthy
        assert get()[0] == 503
    finally:
        server.stop()


# --------------------------------------------------------------------------- #
# No inline / duplicate publisher wiring
# --------------------------------------------------------------------------- #


def test_scheduler_reuses_publish_outbox_without_inline_send_task() -> None:
    src = (ROOT / "src" / "risk_platform" / "scheduler.py").read_text(encoding="utf-8")
    assert "publish_outbox" in src  # reuses the approved publisher
    assert "send_task" not in src  # no inline broker publish / no duplicate publisher


def test_publish_outbox_has_no_request_path_or_worker_caller() -> None:
    for forbidden in ("worker.py", "main.py", "composition.py"):
        text_src = (ROOT / "src" / "risk_platform" / forbidden).read_text(encoding="utf-8")
        assert "publish_outbox" not in text_src
    grep = subprocess.run(
        ["grep", "-rl", "--include=*.py", "publish_outbox", str(ROOT / "src" / "risk_platform")],
        capture_output=True,
        text=True,
        check=False,
    )
    callers = {Path(line).name for line in grep.stdout.split() if line}
    # dispatcher.py defines publish_outbox; scheduler.py is the sole production caller.
    assert callers == {"scheduler.py", "dispatcher.py"}


# --------------------------------------------------------------------------- #
# PostgreSQL 16 validation
# --------------------------------------------------------------------------- #


@pytest.fixture
def scheduler_schema() -> Iterator[Connection]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL T046 validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t046_{uuid.uuid4().hex}"
    admin = create_engine(sync_url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as conn:
            config = Config(ROOT / "alembic.ini")
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
            conn.commit()
            yield conn
            conn.rollback()
            command.check(config)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _async_factory(
    connection: Connection,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    schema = connection.scalar(text("SELECT current_schema()"))
    assert isinstance(schema, str)
    url = os.environ["TEST_DATABASE_URL"]
    async_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    engine = create_database_engine(
        f"{async_url}?options=-csearch_path%3D{schema}", pool_pre_ping=False
    )
    return engine, create_session_factory(engine)


def _seed_outbox_row(connection: Connection) -> uuid.UUID:
    task_id = uuid.uuid4()
    connection.execute(
        text(
            'INSERT INTO durable_tasks (id, kind, status, "idempotencyKey", payload, '
            '"maxAttempts", "attemptCount", "dispatchGeneration", "updatedAt") '
            "VALUES (:id, 'ATTACHMENT_PARSE', 'QUEUED', :key, "
            "'{}'::jsonb, 3, 0, 1, CURRENT_TIMESTAMP)"
        ),
        {"id": task_id, "key": f"t046-{task_id}"},
    )
    connection.execute(
        text('INSERT INTO task_outbox (id, "taskId", "dispatchGeneration") VALUES (:oid, :tid, 1)'),
        {"oid": uuid.uuid4(), "tid": task_id},
    )
    connection.commit()
    return task_id


def test_single_active_advisory_lock_rejects_then_reacquires(scheduler_schema: Connection) -> None:
    engine, _factory = _async_factory(scheduler_schema)

    async def exercise() -> None:
        conn1 = await engine.connect()
        conn2 = await engine.connect()
        try:
            assert await acquire_advisory_lock(conn1, ADVISORY_LOCK_KEY) is True
            # second active instance is rejected while the first holds the session lock
            assert await acquire_advisory_lock(conn2, ADVISORY_LOCK_KEY) is False
            assert await release_advisory_lock(conn1, ADVISORY_LOCK_KEY) is True
            # reacquire succeeds after the first connection releases
            assert await acquire_advisory_lock(conn2, ADVISORY_LOCK_KEY) is True
            assert await release_advisory_lock(conn2, ADVISORY_LOCK_KEY) is True
        finally:
            await conn1.close()
            await conn2.close()

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(dispose_database_engine(engine))


def test_outbox_drain_retries_after_send_failure_then_publishes(
    scheduler_schema: Connection,
) -> None:
    engine, factory = _async_factory(scheduler_schema)
    task_id = _seed_outbox_row(scheduler_schema)
    broker = _Broker(fail_times=1)

    async def exercise() -> None:
        action = make_drain_outbox(factory, broker)
        cadence = Cadence(outbox_drain_seconds=5.0)
        clock = _FakeClock(0.0)
        scheduler = Scheduler(
            actions=[ActionSpec("outbox", action, 5.0)],
            cadence=cadence,
            acquire=_acquire_true,
            release=_release_noop,
            lock_ping=_ping_ok,
            liveness=LivenessState(cadence.liveness_window_seconds),
            clock=clock,
        )
        await scheduler.tick()  # broker fails -> isolated, row stays unpublished
        assert broker.fail_times == 0
        async with factory() as session:
            row = (
                await session.scalars(select(TaskOutbox).where(TaskOutbox.taskId == task_id))
            ).one()
            assert row.publishedAt is None
        clock.t = 1.0
        await scheduler.tick()  # retried on next tick -> published
        async with factory() as session:
            row = (
                await session.scalars(select(TaskOutbox).where(TaskOutbox.taskId == task_id))
            ).one()
            assert row.publishedAt is not None
        assert broker.messages == [("risk_platform.reliability.execute", [str(task_id), 1])]

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(dispose_database_engine(engine))


def test_reconcile_action_runs_against_postgresql(scheduler_schema: Connection) -> None:
    engine, factory = _async_factory(scheduler_schema)

    async def exercise() -> None:
        action = make_reconcile(factory)
        await action()  # empty DB -> reconcile returns 0, no error, no audit writes

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(dispose_database_engine(engine))


def test_mailbox_schedule_action_runs_against_postgresql(scheduler_schema: Connection) -> None:
    engine, factory = _async_factory(scheduler_schema)

    async def exercise() -> None:
        action = make_mailbox_sync(factory)
        await action()  # no enabled mailboxes -> no batches, no error

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(dispose_database_engine(engine))


def test_scheduler_run_loop_against_postgresql(scheduler_schema: Connection) -> None:
    engine, factory = _async_factory(scheduler_schema)

    async def exercise() -> int:
        lock_conn = await engine.connect()

        async def acquire() -> bool:
            return await acquire_advisory_lock(lock_conn, ADVISORY_LOCK_KEY)

        async def release() -> None:
            await release_advisory_lock(lock_conn, ADVISORY_LOCK_KEY)
            await lock_conn.close()

        async def lock_ping() -> None:
            await lock_conn.execute(text("SELECT 1"))
            await lock_conn.commit()

        cadence = Cadence()
        actions = [
            ActionSpec(
                "outbox",
                make_drain_outbox(factory, _Broker()),
                cadence.outbox_drain_seconds,
            ),
            ActionSpec("reconcile", make_reconcile(factory), cadence.reconcile_seconds),
            ActionSpec("mailbox", make_mailbox_sync(factory), cadence.mailbox_sync_seconds),
        ]
        liveness = LivenessState(cadence.liveness_window_seconds)
        stop = asyncio.Event()
        sleeps: list[float] = []
        healthy_during_run: list[bool] = []

        async def sleep(seconds: float) -> None:
            # By the time we sleep, at least one tick has completed (lock held + tick recorded).
            healthy_during_run.append(liveness.healthy(time.monotonic()))
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                stop.set()

        scheduler = Scheduler(
            actions=actions,
            cadence=cadence,
            acquire=acquire,
            release=release,
            lock_ping=lock_ping,
            liveness=liveness,
            sleep=sleep,
        )
        code = await scheduler.run(stop)
        assert code == 0
        assert healthy_during_run  # at least one tick ran
        assert all(healthy_during_run)  # lock held + recent tick throughout the run
        return code

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(dispose_database_engine(engine))


def test_outbox_drain_publishes_to_real_redis_broker(scheduler_schema: Connection) -> None:
    """The drain tick's send_task reaches a real Redis 7 broker (delivery transport)."""

    broker_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    # Use an isolated Redis DB so the probe never pollutes the shared broker.
    probe_broker = re.sub(r"/\d+(?:\?.*)?$", "/15", broker_url)
    try:
        redis_lib.from_url(probe_broker).ping()  # type: ignore[no-untyped-call]
    except redis_lib.RedisError:
        pytest.skip("Redis 7 不可用; T046 send-path validation 未执行")

    engine, factory = _async_factory(scheduler_schema)
    task_id = _seed_outbox_row(scheduler_schema)
    # Celery resolves broker_url from the CELERY_BROKER_URL env var at send time,
    # so point it at the isolated DB for the whole publish, then restore it.
    saved_broker = os.environ.get("CELERY_BROKER_URL")
    os.environ["CELERY_BROKER_URL"] = probe_broker
    client = redis_lib.from_url(probe_broker)  # type: ignore[no-untyped-call]
    client.delete("celery")  # ensure clean queue
    try:
        celery = create_celery_app()

        async def exercise() -> None:
            action = make_drain_outbox(factory, celery)
            await action()
            async with factory() as session:
                row = (
                    await session.scalars(select(TaskOutbox).where(TaskOutbox.taskId == task_id))
                ).one()
                assert row.publishedAt is not None

        asyncio.run(exercise())
        # publish_outbox sent one message to the default "celery" queue on Redis DB 15.
        assert client.llen("celery") == 1
    finally:
        client.flushdb()
        if saved_broker is None:
            os.environ.pop("CELERY_BROKER_URL", None)
        else:
            os.environ["CELERY_BROKER_URL"] = saved_broker
        asyncio.run(dispose_database_engine(engine))
