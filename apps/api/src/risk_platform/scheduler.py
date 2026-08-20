"""Production scheduler entrypoint (ADR 0030).

A single independent process that acquires a PostgreSQL session-level advisory
lock (single-active) and periodically drives three existing service entry
points — the transactional-outbox drain (``publish_outbox``), lease
reconciliation (``reconcile``) and the scheduled mailbox sync
(``schedule_enabled_syncs``). It is the *only* production publisher: the
request path writes PostgreSQL alone (``enqueue_task``) and never touches the
broker, so there is no DB/Celery dual-write and PostgreSQL stays the sole
authority; Redis/Celery is delivery/execution transport only.

The scheduler does not register a Celery executor, configure Celery Beat, or
construct any domain service. It re-uses the five public entry points
read-only and writes no audit (ADR 0017); its logs carry no secret, mail
content or task payload (ADR 0014/0007). It does not edit ``composition.py``,
``celery_app.py``, ``worker.py`` or ``main.py`` (ADR 0030 §2).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from celery import Celery
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from risk_platform.db import (
    DatabaseConfigurationError,
    create_database_engine,
    create_session_factory,
    database_url,
    dispose_database_engine,
    transaction,
)
from risk_platform.mailbox.sync import schedule_enabled_syncs
from risk_platform.reliability.celery_app import celery_app
from risk_platform.reliability.core import reconcile
from risk_platform.reliability.dispatcher import publish_outbox

logger = logging.getLogger(__name__)

# 'risk_sch' packed as 8 ASCII bytes — a fixed, stable single-active fence.
ADVISORY_LOCK_KEY: Final[int] = 0x7269736B5F736368

DEFAULT_OUTBOX_DRAIN_SECONDS: Final[float] = 5.0
DEFAULT_RECONCILE_SECONDS: Final[float] = 30.0
DEFAULT_MAILBOX_SYNC_SECONDS: Final[float] = 300.0
DEFAULT_POLL_SECONDS: Final[float] = 1.0
STARTUP_CONNECT_RETRIES: Final[int] = 5
STARTUP_CONNECT_BACKOFF_SECONDS: Final[float] = 2.0
DEFAULT_LIVENESS_HOST: Final[str] = "0.0.0.0"
DEFAULT_LIVENESS_PORT: Final[int] = 9191


class SchedulerConfigurationError(RuntimeError):
    """Raised on an invalid scheduler startup configuration (fail-fast, no retry)."""


@dataclass(frozen=True)
class Cadence:
    """Operational cadence defaults (ADR 0030 §3); not performance SLOs."""

    outbox_drain_seconds: float = DEFAULT_OUTBOX_DRAIN_SECONDS
    reconcile_seconds: float = DEFAULT_RECONCILE_SECONDS
    mailbox_sync_seconds: float = DEFAULT_MAILBOX_SYNC_SECONDS
    poll_seconds: float = DEFAULT_POLL_SECONDS

    @property
    def liveness_window_seconds(self) -> float:
        """A tick is healthy if it ran within 2x the outbox drain interval."""

        return 2.0 * self.outbox_drain_seconds

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Cadence:
        env = os.environ if environ is None else environ

        def _positive(name: str, default: float) -> float:
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            try:
                value = float(raw)
            except ValueError as exc:
                raise SchedulerConfigurationError(f"{name} 不是有效数字: {raw!r}") from exc
            if value <= 0:
                raise SchedulerConfigurationError(f"{name} 必须为正数: {value}")
            return value

        return cls(
            outbox_drain_seconds=_positive(
                "SCHEDULER_OUTBOX_DRAIN_INTERVAL_SECONDS", DEFAULT_OUTBOX_DRAIN_SECONDS
            ),
            reconcile_seconds=_positive(
                "SCHEDULER_RECONCILE_INTERVAL_SECONDS", DEFAULT_RECONCILE_SECONDS
            ),
            mailbox_sync_seconds=_positive(
                "SCHEDULER_MAILBOX_SYNC_INTERVAL_SECONDS", DEFAULT_MAILBOX_SYNC_SECONDS
            ),
            poll_seconds=_positive("SCHEDULER_POLL_INTERVAL_SECONDS", DEFAULT_POLL_SECONDS),
        )


class LivenessState:
    """Thread-safe liveness shared between the async tick loop and the probe thread."""

    def __init__(self, window_seconds: float) -> None:
        self._lock = threading.Lock()
        self._window = window_seconds
        self._lock_held = False
        self._last_tick_monotonic: float | None = None

    def set_lock_held(self, value: bool) -> None:
        with self._lock:
            self._lock_held = value

    def record_tick(self, now: float) -> None:
        with self._lock:
            self._last_tick_monotonic = now

    def healthy(self, now: float) -> bool:
        with self._lock:
            if not self._lock_held or self._last_tick_monotonic is None:
                return False
            return now - self._last_tick_monotonic <= self._window

    def snapshot(self, now: float) -> dict[str, object]:
        with self._lock:
            healthy = bool(
                self._lock_held
                and self._last_tick_monotonic is not None
                and now - self._last_tick_monotonic <= self._window
            )
            return {
                "healthy": healthy,
                "lock_held": self._lock_held,
                "last_tick_monotonic": self._last_tick_monotonic,
                "liveness_window_seconds": self._window,
            }


async def acquire_advisory_lock(conn: AsyncConnection, key: int) -> bool:
    """Acquire a session-level advisory lock; returns False if already held."""

    result = await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    return bool(result.scalar())


async def release_advisory_lock(conn: AsyncConnection, key: int) -> bool:
    """Release the session-level advisory lock held on this connection."""

    result = await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    return bool(result.scalar())


@dataclass(frozen=True)
class ActionSpec:
    """A driven function and the interval that gates it within each tick."""

    name: str
    action: Callable[[], Awaitable[None]]
    interval: float


class Scheduler:
    """Single-active tick loop driving the three reliability entry points.

    Dependencies (lock acquisition, driven actions, clock, sleep) are injected
    so the cadence, isolation and shutdown contract is unit-testable without a
    database. Production wiring lives in :func:`main`.
    """

    def __init__(
        self,
        *,
        actions: Sequence[ActionSpec],
        cadence: Cadence,
        acquire: Callable[[], Awaitable[bool]],
        release: Callable[[], Awaitable[None]],
        lock_ping: Callable[[], Awaitable[None]],
        liveness: LivenessState,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._actions = list(actions)
        self._cadence = cadence
        self._acquire = acquire
        self._release = release
        self._lock_ping = lock_ping
        self._liveness = liveness
        self._clock = clock
        self._sleep = sleep or _default_sleep
        self._last_run: dict[str, float] = {spec.name: -math.inf for spec in self._actions}

    async def run(self, stop: asyncio.Event) -> int:
        """Acquire the lock, tick until ``stop`` is set, then release.

        Returns a process exit code: ``0`` on graceful shutdown, ``1`` when the
        lock could not be acquired (another scheduler is active) or was lost.
        """

        acquired = await self._acquire()
        if not acquired:
            logger.error("advisory lock 已被持有, 另一个 scheduler 已激活; 退出")
            return 1
        self._liveness.set_lock_held(True)
        logger.info("scheduler 获取 advisory lock, 进入 tick 循环")
        exit_code = 0
        try:
            while not stop.is_set():
                if not await self.tick():
                    exit_code = 1
                    break
                if stop.is_set():
                    break
                await self._sleep(self._cadence.poll_seconds)
        finally:
            self._liveness.set_lock_held(False)
            try:
                await self._release()
            except Exception:
                logger.exception("释放 advisory lock 失败 (连接关闭仍会释放 session-level lock)")
        return exit_code

    async def tick(self) -> bool:
        """Run one pass: keepalive the lock, then fire each due action.

        Returns ``False`` (signal to stop and restart) when the lock connection
        is unreachable. Per-function failures are isolated: a raised action is
        logged and its run time is not advanced, so it stays due for retry on
        the next tick; the other actions and the loop are unaffected.
        """

        try:
            await self._lock_ping()
        except Exception:
            logger.exception("lock 连接不可用, 标记不健康并退出以触发重启")
            return False
        now = self._clock()
        for spec in self._actions:
            if now - self._last_run[spec.name] >= spec.interval:
                try:
                    await spec.action()
                except Exception:
                    logger.exception("scheduler 动作失败 name=%s (已隔离, 继续)", spec.name)
                else:
                    self._last_run[spec.name] = self._clock()
        self._liveness.record_tick(self._clock())
        return True


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _interruptible_sleep(seconds: float, stop: asyncio.Event) -> None:
    """Sleep ``seconds`` but wake promptly when ``stop`` is set (SIGTERM)."""

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=seconds)


def make_drain_outbox(
    session_factory: async_sessionmaker[AsyncSession], celery: Celery
) -> Callable[[], Awaitable[None]]:
    """Wrap ``publish_outbox`` in a caller-owned transaction (the publisher)."""

    async def action() -> None:
        async with transaction(session_factory) as session:
            await publish_outbox(session, celery)

    return action


def make_reconcile(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], Awaitable[None]]:
    """Wrap ``reconcile`` in a caller-owned transaction."""

    async def action() -> None:
        async with transaction(session_factory) as session:
            await reconcile(session)

    return action


def make_mailbox_sync(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], Awaitable[None]]:
    """Drive ``schedule_enabled_syncs`` (manages its own transaction)."""

    async def action() -> None:
        await schedule_enabled_syncs(session_factory)

    return action


async def connect_with_retry(
    engine: AsyncEngine,
    *,
    retries: int = STARTUP_CONNECT_RETRIES,
    backoff: float = STARTUP_CONNECT_BACKOFF_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncConnection:
    """Connect to PostgreSQL with bounded retry; crash (raise) only past retries."""

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await engine.connect()
        except DBAPIError as exc:
            last_exc = exc
            logger.warning("PostgreSQL 连接失败 attempt=%s/%s", attempt, retries)
            if attempt < retries:
                await sleep(backoff * attempt)
    assert last_exc is not None
    raise last_exc


async def acquire_lock_with_retry(
    conn: AsyncConnection,
    key: int,
    *,
    retries: int = STARTUP_CONNECT_RETRIES,
    backoff: float = STARTUP_CONNECT_BACKOFF_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Acquire the advisory lock, retrying only on DB unreachability.

    A ``False`` result (lock already held by another scheduler) is returned
    immediately — single-active rejection is fail-fast, not retryable.
    """

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            acquired = await acquire_advisory_lock(conn, key)
            await conn.commit()
            return acquired
        except DBAPIError as exc:
            last_exc = exc
            logger.warning("advisory lock 获取失败 attempt=%s/%s", attempt, retries)
            if attempt < retries:
                await sleep(backoff * attempt)
    assert last_exc is not None
    raise last_exc


def _build_liveness_handler(
    liveness: LivenessState, clock: Callable[[], float]
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            snap = liveness.snapshot(clock())
            body = json.dumps(snap).encode()
            code = 200 if bool(snap["healthy"]) else 503
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return
    return _Handler


class LivenessHTTPServer:
    """Minimal stdlib HTTP probe; no extra dependencies (ADR 0030 §7)."""

    def __init__(
        self,
        liveness: LivenessState,
        host: str = DEFAULT_LIVENESS_HOST,
        port: int = DEFAULT_LIVENESS_PORT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._httpd = ThreadingHTTPServer((host, port), _build_liveness_handler(liveness, clock))
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="scheduler-liveness", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _liveness_sleep(stop: asyncio.Event) -> Callable[[float], Awaitable[None]]:
    async def _sleep(seconds: float) -> None:
        await _interruptible_sleep(seconds, stop)

    return _sleep


async def async_main() -> int:
    """Construct production dependencies and run the scheduler."""

    try:
        cadence = Cadence.from_env()
    except SchedulerConfigurationError:
        logger.exception("scheduler 启动配置错误")
        return 2
    try:
        engine = create_database_engine(database_url())
    except DatabaseConfigurationError:
        logger.exception("DATABASE_URL 配置错误")
        return 2

    session_factory = create_session_factory(engine)
    try:
        lock_conn = await connect_with_retry(engine)
    except DBAPIError:
        logger.exception("PostgreSQL 不可达, 超过有界重试, 退出以触发重启")
        await dispose_database_engine(engine)
        return 1

    async def acquire() -> bool:
        return await acquire_lock_with_retry(lock_conn, ADVISORY_LOCK_KEY)

    async def release() -> None:
        try:
            await release_advisory_lock(lock_conn, ADVISORY_LOCK_KEY)
        finally:
            await lock_conn.close()

    async def lock_ping() -> None:
        await lock_conn.execute(text("SELECT 1"))
        await lock_conn.commit()

    actions = [
        ActionSpec(
            "outbox_drain",
            make_drain_outbox(session_factory, celery_app),
            cadence.outbox_drain_seconds,
        ),
        ActionSpec("reconcile", make_reconcile(session_factory), cadence.reconcile_seconds),
        ActionSpec(
            "mailbox_sync",
            make_mailbox_sync(session_factory),
            cadence.mailbox_sync_seconds,
        ),
    ]
    liveness = LivenessState(cadence.liveness_window_seconds)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    host = os.environ.get("SCHEDULER_LIVENESS_HOST", DEFAULT_LIVENESS_HOST)
    port = int(os.environ.get("SCHEDULER_LIVENESS_PORT", str(DEFAULT_LIVENESS_PORT)))
    try:
        server = LivenessHTTPServer(liveness, host, port)
    except OSError:
        logger.exception("liveness 探针监听 %s:%s 失败", host, port)
        await lock_conn.close()
        await dispose_database_engine(engine)
        return 1
    server.start()
    logger.info("scheduler liveness 探针监听 %s:%s", host, port)

    scheduler = Scheduler(
        actions=actions,
        cadence=cadence,
        acquire=acquire,
        release=release,
        lock_ping=lock_ping,
        liveness=liveness,
        sleep=_liveness_sleep(stop),
    )
    try:
        return await scheduler.run(stop)
    finally:
        server.stop()
        await dispose_database_engine(engine)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("SCHEDULER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        code = asyncio.run(async_main())
    except KeyboardInterrupt:
        code = 0
    except DBAPIError:
        logger.exception("PostgreSQL 不可达, 退出以触发重启")
        code = 1
    sys.exit(code)


__all__ = [
    "ADVISORY_LOCK_KEY",
    "ActionSpec",
    "Cadence",
    "LivenessHTTPServer",
    "LivenessState",
    "Scheduler",
    "SchedulerConfigurationError",
    "acquire_advisory_lock",
    "acquire_lock_with_retry",
    "async_main",
    "connect_with_retry",
    "main",
    "make_drain_outbox",
    "make_mailbox_sync",
    "make_reconcile",
    "release_advisory_lock",
]
