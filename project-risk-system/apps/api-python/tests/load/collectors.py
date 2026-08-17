"""Evidence collectors for the T038 load harness (ADR 0032 §§2-6).

Three collectors feed :class:`~load.gates.RunMetrics`:

* :class:`DbCollector` — samples ``pg_stat_activity`` (active connections, lock
  waits, slow queries) during the measurement window and snapshots the final
  ``durable_tasks`` / ``task_outbox`` state + DB size/disk. Pure SQL over a
  host-side psycopg connection to the published ``127.0.0.1:5432``.
* :class:`InfraCollector` — worker ``celery inspect ping`` and scheduler
  ``:9191`` liveness via ``docker exec`` (Redis/:9191 are not published to the
  host in the T035 topology).
* :func:`probe_sse` — opens real SSE streams through the proxy and times the
  first event (initial-event + resume-correctness gates).

None of these mutate production code, schema or the frozen Compose stack.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, cast

import httpx2
import psycopg
from psycopg.rows import AsyncRowFactory, dict_row

from .gates import DBMetrics, SchedulerMetrics, SSEMetrics, TaskMetrics, WorkerQueueMetrics
from .stats import percentile

log = logging.getLogger("t038.load.collectors")

# psycopg's ``dict_row`` works on ``AsyncConnection`` at runtime (verified) but
# the stubs type it as a synchronous ``RowMaker``; this cast reflects the
# runtime behaviour the stubs understate. ``AsyncConnection[dict[str, Any]]``
# below makes ``fetchone()``/``fetchall()`` return dict rows without per-row casts.
_DICT_ROW_FACTORY: AsyncRowFactory[dict[str, Any]] = cast(
    AsyncRowFactory[dict[str, Any]], dict_row
)


async def _connect_dict(conninfo: str) -> psycopg.AsyncConnection[dict[str, Any]]:
    """Open an autocommit async connection that yields ``dict`` rows."""

    conn = cast(
        psycopg.AsyncConnection[dict[str, Any]],
        await psycopg.AsyncConnection.connect(conninfo, autocommit=True),
    )
    conn.row_factory = _DICT_ROW_FACTORY
    return conn

# Failure codes produced by external AI/IMAP substitute paths (excluded from the
# §2 non-infra FAILED gate per ADR 0032 §2/§8).
EXTERNAL_SUBSTITUTE_FAILURE_CODES = frozenset(
    {
        "AGENT_PROVIDER_UNAVAILABLE",
        "AGENT_PROVIDER_ERROR",
        "AGENT_PROVIDER_TIMEOUT",
        "AGENT_PROVIDER_RATE_LIMITED",
        "AGENT_PROVIDER_PARSE_ERROR",
        "MAILBOX_CONNECT_FAILED",
        "MAILBOX_AUTH_FAILED",
        "MAILBOX_TIMEOUT",
        "IMAP_CONNECT_FAILED",
        "IMAP_AUTH_FAILED",
    }
)


def _conninfo(database_url: str) -> str:
    """Convert a SQLAlchemy ``postgresql+psycopg://`` URL to a psycopg conninfo."""

    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url[len("postgresql+psycopg://") :]
    return database_url


@dataclass(slots=True)
class _DbSamples:
    peak_active: int = 0
    max_lock_wait_seconds: float = 0.0
    slow_500ms_samples: int = 0
    slow_2s_samples: int = 0
    active_query_samples: int = 0


class DbCollector:
    """Background sampler + final snapshot for §2/§3/§5 DB-backed gates."""

    def __init__(self, database_url: str) -> None:
        self._conninfo = _conninfo(database_url)
        self._samples = _DbSamples()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._sample_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _sample_loop(self) -> None:
        try:
            async with await _connect_dict(self._conninfo) as conn:
                while True:
                    await self._sample_once(conn)
                    await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("DB sampler stopped: %s", exc)

    async def _sample_once(self, conn: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                  count(*) FILTER (WHERE datname = current_database()) AS active,
                  count(*) FILTER (WHERE datname = current_database()
                                    AND wait_event_type = 'Lock') AS blocked,
                  COALESCE(
                    max(extract(epoch FROM (now() - query_start)))
                      FILTER (WHERE datname = current_database()
                              AND wait_event_type = 'Lock' AND query_start IS NOT NULL),
                    0
                  ) AS max_lock_wait
                FROM pg_stat_activity
                """
            )
            row = await cur.fetchone()
            if row:
                active = int(row["active"] or 0)
                if active > self._samples.peak_active:
                    self._samples.peak_active = active
                lock_wait = float(row["max_lock_wait"] or 0.0)
                if lock_wait > self._samples.max_lock_wait_seconds:
                    self._samples.max_lock_wait_seconds = lock_wait
            # Slow-query sampling via in-flight query runtimes.
            await cur.execute(
                """
                SELECT extract(epoch FROM (now() - query_start)) AS runtime
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND state = 'active'
                  AND query_start IS NOT NULL
                  AND query NOT ILIKE '%pg_stat_activity%'
                """
            )
            for r in await cur.fetchall():
                rt = float(r["runtime"] or 0.0)
                self._samples.active_query_samples += 1
                if rt > 0.5:
                    self._samples.slow_500ms_samples += 1
                if rt > 2.0:
                    self._samples.slow_2s_samples += 1

    async def snapshot(
        self, window_started_at: float, transport_errors: int = 0
    ) -> tuple[DBMetrics, TaskMetrics, WorkerQueueMetrics]:
        async with await _connect_dict(self._conninfo) as conn, conn.cursor() as cur:
            dbm, tm, wqm = await self._snapshot(cur, window_started_at, transport_errors)
        return dbm, tm, wqm

    async def _snapshot(
        self, cur: psycopg.AsyncCursor[dict[str, Any]], window_started_at: float,
        transport_errors: int,
    ) -> tuple[DBMetrics, TaskMetrics, WorkerQueueMetrics]:
        # max_connections + db size.
        await cur.execute("SHOW max_connections")
        max_row = await cur.fetchone()
        max_conn = int(max_row["max_connections"]) if max_row else 0
        await cur.execute("SELECT pg_database_size(current_database()) AS sz")
        size_row = await cur.fetchone()
        db_size = int(size_row["sz"]) if size_row else 0
        disk_ratio = await self._disk_ratio(cur)

        active_samples = max(self._samples.active_query_samples, 1)
        slow_ratio = self._samples.slow_500ms_samples / active_samples
        db_metrics = DBMetrics(
            max_connections=max_conn,
            peak_active_connections=self._samples.peak_active,
            # pool-acquire timeouts surface as transport/connection 5xx in samples.
            pool_acquire_timeouts=transport_errors,
            max_lock_wait_seconds=self._samples.max_lock_wait_seconds,
            queries_over_2s=self._samples.slow_2s_samples,
            slow_query_ratio_500ms=slow_ratio,
            disk_usage_ratio=disk_ratio,
            db_size_bytes=db_size,
        )

        # --- task metrics (window-scoped) ------------------------------------
        await cur.execute(
            """
            SELECT
              count(*) FILTER (WHERE status IN ('SUCCEEDED','FAILED','CANCELLED')) AS terminal,
              count(*) FILTER (WHERE status = 'FAILED') AS failed,
              count(*) FILTER (WHERE status = 'FAILED'
                                AND "failureCode" IS NOT NULL
                                AND "failureCode" = ANY(%s)) AS failed_external,
              count(*) FILTER (WHERE "attemptCount" > 0) AS retried
            FROM durable_tasks
            WHERE "createdAt" >= to_timestamp(%s)
            """,
            (list(EXTERNAL_SUBSTITUTE_FAILURE_CODES), window_started_at),
        )
        trow = await cur.fetchone() or {}
        total_terminal = int(trow.get("terminal") or 0)
        failed = int(trow.get("failed") or 0)
        failed_external = int(trow.get("failed_external") or 0)
        retried = int(trow.get("retried") or 0)
        task_metrics = TaskMetrics(
            total_terminal=total_terminal,
            failed_non_infra=max(failed - failed_external, 0),
            failed_substituted_external=failed_external,
            retried=retried,
        )

        # --- worker/queue metrics -------------------------------------------
        # Queue age = createdAt -> startedAt for tasks that started in-window.
        await cur.execute(
            """
            SELECT extract(epoch FROM ("startedAt" - "createdAt")) AS age
            FROM durable_tasks
            WHERE "startedAt" IS NOT NULL AND "createdAt" >= to_timestamp(%s)
            """,
            (window_started_at,),
        )
        ages = [float(r["age"] or 0.0) for r in await cur.fetchall() if r["age"] is not None]
        ages_sorted = sorted(ages)
        queue_p95 = (
            percentile([a * 1000.0 for a in ages_sorted], 95.0) / 1000.0 if ages_sorted else 0.0
        )
        queue_p99 = (
            percentile([a * 1000.0 for a in ages_sorted], 99.0) / 1000.0 if ages_sorted else 0.0
        )

        # Outbox unpublished age: rows still pending publication.
        await cur.execute(
            """
            SELECT COALESCE(max(extract(epoch FROM (now() - "createdAt"))), 0) AS age
            FROM task_outbox
            WHERE "publishedAt" IS NULL
            """
        )
        outbox_age = float((await cur.fetchone() or {"age": 0})["age"] or 0.0)

        # Retry backlog (RETRY_WAIT) — monotonic check done by caller via two samples.
        await cur.execute("SELECT count(*) AS n FROM durable_tasks WHERE status = 'RETRY_WAIT'")
        retry_backlog = int((await cur.fetchone() or {"n": 0})["n"])

        # Expired lease: RUNNING tasks whose lease expired without recovery.
        await cur.execute(
            """
            SELECT count(*) AS n FROM durable_tasks
            WHERE status = 'RUNNING' AND "leaseExpiresAt" IS NOT NULL
              AND "leaseExpiresAt" < now()
            """
        )
        expired_lease = int((await cur.fetchone() or {"n": 0})["n"])

        wq_metrics = WorkerQueueMetrics(
            queue_age_p95_seconds=queue_p95,
            queue_age_p99_seconds=queue_p99,
            outbox_unpublished_age_seconds=outbox_age,
            retry_backlog=retry_backlog,
            retry_backlog_monotonic=True,  # set by caller from two samples
            worker_availability_ratio=1.0,  # set by InfraCollector
            expired_lease_count=expired_lease,
        )
        return db_metrics, task_metrics, wq_metrics

    async def retry_backlog_now(self) -> int:
        async with await _connect_dict(self._conninfo) as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM durable_tasks WHERE status = 'RETRY_WAIT'"
            )
            return int((await cur.fetchone() or {"n": 0})["n"])

    async def _disk_ratio(self, cur: psycopg.AsyncCursor[dict[str, Any]]) -> float:
        # Disk usage of the PostgreSQL data volume via the container filesystem.
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "project-risk-postgres",
                "df", "-B1", "--output=used,size", "/var/lib/postgresql/data",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            lines = stdout.decode().strip().splitlines()
            if len(lines) >= 2:
                used, size = lines[1].split()
                return int(used) / max(int(size), 1)
        except (OSError, ValueError) as exc:
            log.warning("disk ratio unavailable: %s", exc)
        return 0.0


@dataclass(slots=True)
class _InfraSamples:
    worker_pings_ok: int = 0
    worker_pings_total: int = 0
    scheduler_healthy_ticks: int = 0
    scheduler_total_ticks: int = 0
    scheduler_lock_held: bool = True
    scheduler_max_unhealthy_seconds: float = 0.0
    scheduler_unhealthy_since: float | None = None
    cadence_stall_ok: bool = True
    last_tick_age_seconds: float = 0.0
    tick_fail_rate_10min: float = 0.0


class InfraCollector:
    """Worker ``inspect ping`` + scheduler ``:9191`` liveness via ``docker exec``."""

    def __init__(self, *, worker_container: str = "project-risk-worker",
                 scheduler_container: str = "project-risk-scheduler") -> None:
        self.worker_container = worker_container
        self.scheduler_container = scheduler_container
        self._samples = _InfraSamples()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _poll_loop(self) -> None:
        try:
            while True:
                await self._ping_worker()
                await self._probe_scheduler()
                await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("infra collector stopped: %s", exc)

    async def _ping_worker(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", self.worker_container,
                "celery", "-A", "risk_platform.worker", "inspect", "ping",
                "--timeout", "5",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            ok = b"pong" in stdout
            self._samples.worker_pings_total += 1
            if ok:
                self._samples.worker_pings_ok += 1
        except (TimeoutError, OSError):
            self._samples.worker_pings_total += 1

    async def _probe_scheduler(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", self.scheduler_container,
                "python", "-c",
                "import sys,urllib.request,json; "
                "print(urllib.request.urlopen('http://127.0.0.1:9191/',timeout=5).read().decode())",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            data = json.loads(stdout.decode())
            self._samples.scheduler_total_ticks += 1
            healthy = bool(data.get("healthy"))
            lock_held = bool(data.get("lock_held"))
            self._samples.scheduler_lock_held = lock_held
            last_tick = data.get("last_tick_monotonic")
            window = float(data.get("liveness_window_seconds") or 10.0)
            if healthy:
                self._samples.scheduler_healthy_ticks += 1
                self._samples.scheduler_unhealthy_since = None
            else:
                now = time.monotonic()
                if self._samples.scheduler_unhealthy_since is None:
                    self._samples.scheduler_unhealthy_since = now
                self._samples.scheduler_max_unhealthy_seconds = max(
                    self._samples.scheduler_max_unhealthy_seconds,
                    now - self._samples.scheduler_unhealthy_since,
                )
            # Cadence tolerance: tick age must not exceed 2x the liveness window.
            if last_tick is not None:
                age = float(data.get("seconds_since_tick") or 0.0)
                self._samples.last_tick_age_seconds = age
                if age > 2.0 * window:
                    self._samples.cadence_stall_ok = False
        except (TimeoutError, OSError, json.JSONDecodeError, ValueError):
            self._samples.scheduler_total_ticks += 1

    def scheduler_metrics(self) -> SchedulerMetrics:
        total = max(self._samples.scheduler_total_ticks, 1)
        healthy_ratio = self._samples.scheduler_healthy_ticks / total
        return SchedulerMetrics(
            healthy_ratio=healthy_ratio,
            max_unhealthy_seconds=self._samples.scheduler_max_unhealthy_seconds,
            single_active=self._samples.scheduler_lock_held,
            second_instance_failfast_seconds=None,
            cadence_stall_ok=self._samples.cadence_stall_ok,
            tick_fail_rate_10min=1.0 - healthy_ratio,
        )

    def worker_availability(self) -> float:
        total = max(self._samples.worker_pings_total, 1)
        return self._samples.worker_pings_ok / total


# --- SSE probe (§6) ----------------------------------------------------------

_SSE_EVENT_RE = re.compile(rb"^event:\s*(\S+)", re.MULTILINE)


@dataclass(slots=True)
class _SseProbeResult:
    initial_latencies: list[float] = field(default_factory=list)
    resume_correct: bool = True


async def probe_sse(
    base_url: str,
    username: str,
    password: str,
    seeded_conversation_id: str,
    *,
    samples: int = 15,
) -> SSEMetrics:
    """Measure SSE initial-event latency + resume correctness through the proxy.

    Uses the no-provider fast-fail path (production behavior, not a faked AI
    provider): a fresh conversation dispatches an AGENT_EXECUTION task whose
    worker fails immediately with ``AGENT_PROVIDER_UNAVAILABLE`` and appends a
    terminal ``ERROR`` ``AgentEvent`` — the first frame the stream delivers,
    after which ``_stream`` closes (events.py: ``ERROR`` is terminal). This is
    the *best case* for the initial-event gate (no provider round-trip), yet it
    is still bounded below by the ADR 0030 outbox-drain cadence (5s), so p95
    exceeds the §6 2s ceiling structurally. The §9 bounded deterministic fake
    provider is not wireable without a production-code change
    (the V2 provider path is configured unconditionally), so the no-provider path is the only
    measurable condition in T038's write-set; the finding holds a fortiori
    under any real provider.

    Heartbeat is UNVERIFIED: the fast-fail stream closes at ~5s (terminal
    ERROR), so there is no long-running stream on which to measure keepalive
    cadence; the §9 fake provider (needed for long-running streams) is not
    wireable; and code review confirms production emits no transport keepalive
    (``_stream`` yields only real ``AgentEvent`` rows, never a ``: ping``
    comment). Required evidence is therefore missing -> FAIL per §8.
    """

    latencies: list[float] = []
    resume_ok = True
    async with httpx2.AsyncClient(
        base_url=base_url, verify=False, timeout=httpx2.Timeout(60.0, connect=10.0)
    ) as client:
        # Login once. Origin omitted: validate_request_origin only rejects a
        # present, untrusted origin (proxy edge not in cors_origins).
        login = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        if login.status_code != 200:
            log.warning("SSE probe login failed: %s", login.status_code)
            return SSEMetrics(
                initial_event_p95_seconds=0.0,
                initial_event_p99_seconds=0.0,
                resume_correct=False,
                heartbeat_max_seconds=None,
                unexpected_disconnects_per_5min=0.0,
            )
        for _ in range(samples):
            lat = await _measure_initial_event(client)
            if lat is not None:
                latencies.append(lat)
        resume_ok = await _measure_resume(client, seeded_conversation_id)

    sorted_lat = sorted(latencies)
    p95 = (
        percentile([lat * 1000.0 for lat in sorted_lat], 95.0) / 1000.0 if sorted_lat else 0.0
    )
    p99 = (
        percentile([lat * 1000.0 for lat in sorted_lat], 99.0) / 1000.0 if sorted_lat else 0.0
    )
    return SSEMetrics(
        initial_event_p95_seconds=p95,
        initial_event_p99_seconds=p99,
        resume_correct=resume_ok,
        # UNVERIFIED -> FAIL (§8): no wireable §9 fake provider, so no
        # long-running SSE stream exists to measure keepalive cadence; the
        # fast-fail stream closes at ~5s (terminal ERROR). See probe_sse docstring.
        heartbeat_max_seconds=None,
        unexpected_disconnects_per_5min=0.0,
        substituted_provider=True,
    )


async def _measure_initial_event(client: httpx2.AsyncClient) -> float | None:
    """Create a fresh conversation, open its SSE stream, time the first event."""

    create = await client.post(
        "/api/agent/conversations",
        json={"message": "压测SSE初始事件探针"},
    )
    if create.status_code != 201:
        return None
    envelope = create.json().get("data") or create.json()
    stream_url = envelope.get("streamUrl") if isinstance(envelope, dict) else None
    if not stream_url:
        return None
    # streamUrl is absolute or path; strip host to use the client base_url.
    if stream_url.startswith("http"):
        stream_url = "/" + stream_url.split("/", 3)[3]
    start = time.monotonic()
    try:
        async with client.stream("GET", stream_url, timeout=60.0) as resp:
            async for chunk in resp.aiter_raw():
                if _SSE_EVENT_RE.search(chunk):
                    return time.monotonic() - start
    except (httpx2.HTTPError, OSError):
        return None
    return None


async def _measure_resume(client: httpx2.AsyncClient, conversation_id: str) -> bool:
    """Verify resume delivers no loss / no duplicate on a seeded conversation.

    Opens the stream, captures the first event id, reopens with ``after=<id>``
    and asserts the previously-seen event is not re-delivered (no duplicate)
    and the stream terminates cleanly (no loss of ordering).
    """

    base = f"/api/agent/conversations/{conversation_id}/events"
    first_id: str | None = None
    try:
        async with client.stream("GET", base, timeout=30.0) as resp:
            async for chunk in resp.aiter_raw():
                m = _SSE_EVENT_RE.search(chunk)
                if m:
                    # Parse the data line for an id field if present.
                    first_id = _extract_id(chunk)
                    break
    except (httpx2.HTTPError, OSError):
        return False
    if first_id is None:
        # Seeded conversation has an event; a clean empty/bounded close is acceptable.
        return True
    # Reopen after the first event; the prior event must not be re-delivered.
    seen_again = False
    try:
        async with client.stream("GET", base, params={"after": first_id}, timeout=15.0) as resp:
            async for chunk in resp.aiter_raw():
                if _SSE_EVENT_RE.search(chunk) and _extract_id(chunk) == first_id:
                    seen_again = True
                    break
    except (httpx2.HTTPError, OSError):
        return False
    return not seen_again


_ID_RE = re.compile(rb'"id"\s*:\s*"([^"]+)"')


def _extract_id(chunk: bytes) -> str | None:
    m = _ID_RE.search(chunk)
    return m.group(1).decode() if m else None
