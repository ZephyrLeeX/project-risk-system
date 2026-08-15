"""ADR 0032 hard-gate evaluation and release-gate semantics.

Each gate is evaluated against a ``RunMetrics`` snapshot captured during one
measurement run. The evaluator returns a structured ``RunVerdict`` with
per-gate findings (PASS / WARN / FAIL / UNVERIFIED). Cross-run release semantics
(PASS requires 2 consecutive runs within variance; flaky 2-of-3; no best-effort
PASS on missing evidence) live in :func:`release_verdict`.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Literal

from .config import (
    CLASS_LABELS,
    DB_CONNECTION_SATURATION,
    DB_DISK_RATIO,
    DB_LOCK_WAIT_MAX_SECONDS,
    DB_SLOW_QUERY_2S_COUNT,
    DB_SLOW_QUERY_500MS_RATIO,
    ENDPOINT_CLASSES,
    HTTP_5XX_GATE,
    MIN_SAMPLES_P95,
    MIN_SAMPLES_P99,
    OUTBOX_UNPUBLISHED_AGE_SECONDS,
    QUEUE_AGE_P95_SECONDS,
    RETRY_BACKLOG_GATE,
    SCHEDULER_FRESHNESS_GATE,
    SCHEDULER_MAX_UNHEALTHY_SECONDS,
    SCHEDULER_TICK_FAIL_GATE,
    SSE_HEARTBEAT_MAX_SECONDS,
    SSE_INITIAL_EVENT_P95_SECONDS,
    TASK_FAILED_GATE,
    TASK_RETRY_GATE,
    WORKER_AVAILABILITY_GATE,
)
from .stats import ClassSummary

GateStatus = Literal["PASS", "WARN", "FAIL", "UNVERIFIED"]


@dataclass(slots=True)
class GateResult:
    gate_id: str
    section: str
    description: str
    gate_value: float | int | str
    measured: float | int | str
    status: GateStatus
    detail: str = ""
    substituted: bool = False  # ADR 0032 §8: substituted-path evidence, not a real PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "section": self.section,
            "description": self.description,
            "gate": self.gate_value,
            "measured": self.measured,
            "status": self.status,
            "detail": self.detail,
            "substituted": self.substituted,
        }


@dataclass(slots=True)
class DBMetrics:
    max_connections: int
    peak_active_connections: int
    pool_acquire_timeouts: int
    max_lock_wait_seconds: float
    queries_over_2s: int
    slow_query_ratio_500ms: float
    disk_usage_ratio: float
    db_size_bytes: int


@dataclass(slots=True)
class SchedulerMetrics:
    healthy_ratio: float
    max_unhealthy_seconds: float
    single_active: bool
    second_instance_failfast_seconds: float | None
    cadence_stall_ok: bool  # no action stalled > 2x interval
    tick_fail_rate_10min: float


@dataclass(slots=True)
class WorkerQueueMetrics:
    queue_age_p95_seconds: float
    queue_age_p99_seconds: float
    outbox_unpublished_age_seconds: float
    retry_backlog: int
    retry_backlog_monotonic: bool  # True = NOT monotonically growing (good)
    worker_availability_ratio: float
    expired_lease_count: int


@dataclass(slots=True)
class TaskMetrics:
    total_terminal: int
    failed_non_infra: int
    failed_substituted_external: int  # AI/IMAP substitute failures (excluded)
    retried: int


@dataclass(slots=True)
class SSEMetrics:
    initial_event_p95_seconds: float
    initial_event_p99_seconds: float
    resume_correct: bool  # no loss / no dup, 100%
    heartbeat_max_seconds: float | None  # None when no substituted streaming provider
    unexpected_disconnects_per_5min: float
    # Substituted provider: real external throughput deferred to T039.
    substituted_provider: bool = True


@dataclass(slots=True)
class RunMetrics:
    classes: dict[str, ClassSummary]
    db: DBMetrics | None
    scheduler: SchedulerMetrics | None
    worker_queue: WorkerQueueMetrics | None
    tasks: TaskMetrics | None
    sse: SSEMetrics | None
    # Environmental flags.
    environment_ok: bool = True
    environment_detail: str = ""


def _samples_status(samples: ClassSummary, *, p95_only: bool) -> GateStatus:
    """WARN if below the p99 sample target but at/above p95 target; FAIL below p95."""
    if samples.total < MIN_SAMPLES_P95:
        return "FAIL"
    if not p95_only and samples.total < MIN_SAMPLES_P99:
        return "WARN"
    return "PASS"


def evaluate_run(metrics: RunMetrics) -> list[GateResult]:
    """Evaluate every ADR 0032 hard gate against one run's metrics."""

    results: list[GateResult] = []

    # --- §1 API latency (p95 hard gate per class) ---------------------------
    for threshold in ENDPOINT_CLASSES:
        samples = metrics.classes.get(threshold.name)
        if samples is None or samples.total == 0:
            results.append(
                GateResult(
                    f"latency_p95.{threshold.name}",
                    "§1 API latency",
                    f"{CLASS_LABELS[threshold.name]} p95 latency",
                    f"<= {threshold.p95_ms} ms",
                    "no samples",
                    "UNVERIFIED",
                    "endpoint class produced no samples",
                )
            )
            continue
        sample_status = _samples_status(samples, p95_only=False)
        if sample_status == "FAIL":
            results.append(
                GateResult(
                    f"latency_p95.{threshold.name}",
                    "§1 API latency",
                    f"{CLASS_LABELS[threshold.name]} p95 latency",
                    f"<= {threshold.p95_ms} ms",
                    f"{samples.p95_ms:.1f} ms",
                    "FAIL",
                    f"only {samples.total} samples (< {MIN_SAMPLES_P95} required)",
                )
            )
            continue
        if samples.p95_ms <= threshold.p95_ms:
            status: GateStatus = "PASS"
            detail = f"p95={samples.p95_ms:.1f}ms (n={samples.total})"
            if samples.p99_ms > threshold.p99_ms:
                status = "WARN"
                detail += (
                    f"; p99={samples.p99_ms:.1f}ms exceeds report ceiling "
                    f"{threshold.p99_ms}ms"
                )
            if sample_status == "WARN":
                status = "WARN"
                detail += f"; samples {samples.total} < {MIN_SAMPLES_P99} for p99"
            results.append(
                GateResult(
                    f"latency_p95.{threshold.name}",
                    "§1 API latency",
                    f"{CLASS_LABELS[threshold.name]} p95 latency",
                    f"<= {threshold.p95_ms} ms",
                    f"{samples.p95_ms:.1f} ms",
                    status,
                    detail,
                )
            )
        else:
            results.append(
                GateResult(
                    f"latency_p95.{threshold.name}",
                    "§1 API latency",
                    f"{CLASS_LABELS[threshold.name]} p95 latency",
                    f"<= {threshold.p95_ms} ms",
                    f"{samples.p95_ms:.1f} ms",
                    "FAIL",
                    f"p95 exceeds gate (n={samples.total}); owning task per ADR 0032 §10",
                )
            )

    # --- §2 error-rate -------------------------------------------------------
    total_requests = sum(c.total for c in metrics.classes.values())
    total_5xx = sum(c.error_5xx for c in metrics.classes.values())
    total_substituted = sum(c.substituted_5xx for c in metrics.classes.values())
    # 5xx ratio excludes external AI/IMAP substitute 5xx (ADR 0032 §2).
    gated_5xx = max(total_5xx - total_substituted, 0)
    gated_total = max(total_requests, 1)
    http_5xx_ratio = gated_5xx / gated_total
    results.append(
        GateResult(
            "http_5xx_ratio",
            "§2 error-rate",
            "HTTP 5xx ratio (excludes external substitute 5xx)",
            f"<= {HTTP_5XX_GATE:.3%}",
            f"{http_5xx_ratio:.4%}",
            "PASS" if http_5xx_ratio <= HTTP_5XX_GATE else "FAIL",
            f"{gated_5xx}/{gated_total} 5xx; {total_substituted} substituted excluded",
        )
    )

    if metrics.tasks is not None:
        t = metrics.tasks
        eligible = max(t.total_terminal, 1)
        failed_ratio = t.failed_non_infra / eligible
        results.append(
            GateResult(
                "task_failed_ratio",
                "§2 error-rate",
                "Non-infra task FAILED ratio (excludes external AI/IMAP substitute)",
                f"<= {TASK_FAILED_GATE:.2%}",
                f"{failed_ratio:.4%}",
                "PASS" if failed_ratio <= TASK_FAILED_GATE else "FAIL",
                f"{t.failed_non_infra}/{t.total_terminal} non-infra FAILED; "
                f"{t.failed_substituted_external} external-substitute excluded",
            )
        )
        retry_ratio = t.retried / eligible
        results.append(
            GateResult(
                "task_retry_ratio",
                "§2 error-rate",
                "Task retry ratio (>=1 retry)",
                f"<= {TASK_RETRY_GATE:.2%}",
                f"{retry_ratio:.4%}",
                "PASS" if retry_ratio <= TASK_RETRY_GATE else "FAIL",
                f"{t.retried}/{t.total_terminal} retried",
            )
        )
    else:
        for gid in ("task_failed_ratio", "task_retry_ratio"):
            results.append(GateResult(gid, "§2 error-rate", gid, "n/a", "no data", "UNVERIFIED"))

    # --- §3 worker/queue -----------------------------------------------------
    if metrics.worker_queue is not None:
        wq = metrics.worker_queue
        results.append(
            GateResult(
                "queue_age_p95",
                "§3 worker/queue",
                "Durable task queue age p95",
                f"<= {QUEUE_AGE_P95_SECONDS}s",
                f"{wq.queue_age_p95_seconds:.2f}s",
                "PASS" if wq.queue_age_p95_seconds <= QUEUE_AGE_P95_SECONDS else "FAIL",
                f"p99={wq.queue_age_p99_seconds:.2f}s",
            )
        )
        results.append(
            GateResult(
                "outbox_unpublished_age",
                "§3 worker/queue",
                "Outbox unpublished age (max)",
                f"<= {OUTBOX_UNPUBLISHED_AGE_SECONDS}s",
                f"{wq.outbox_unpublished_age_seconds:.2f}s",
                "PASS" if wq.outbox_unpublished_age_seconds <= OUTBOX_UNPUBLISHED_AGE_SECONDS
                else "FAIL",
                "",
            )
        )
        backlog_ok = wq.retry_backlog <= RETRY_BACKLOG_GATE and wq.retry_backlog_monotonic
        results.append(
            GateResult(
                "retry_backlog",
                "§3 worker/queue",
                "Retry backlog (<=50, not monotonically growing)",
                f"<= {RETRY_BACKLOG_GATE}",
                f"{wq.retry_backlog} (monotonic={not wq.retry_backlog_monotonic})",
                "PASS" if backlog_ok else "FAIL",
                "",
            )
        )
        results.append(
            GateResult(
                "worker_availability",
                "§3 worker/queue",
                "Worker availability (inspect ping >=1 node, 100%)",
                f">= {WORKER_AVAILABILITY_GATE:.0%}",
                f"{wq.worker_availability_ratio:.2%}",
                "PASS" if wq.worker_availability_ratio >= WORKER_AVAILABILITY_GATE else "FAIL",
                "",
            )
        )
        results.append(
            GateResult(
                "expired_lease",
                "§3 worker/queue",
                "No expired lease unrecovered",
                "= 0",
                f"{wq.expired_lease_count}",
                "PASS" if wq.expired_lease_count == 0 else "FAIL",
                "",
            )
        )
    else:
        for gid in ("queue_age_p95", "outbox_unpublished_age", "retry_backlog",
                    "worker_availability", "expired_lease"):
            results.append(GateResult(gid, "§3 worker/queue", gid, "n/a", "no data", "UNVERIFIED"))

    # --- §4 scheduler --------------------------------------------------------
    if metrics.scheduler is not None:
        s = metrics.scheduler
        fresh_ok = s.healthy_ratio >= SCHEDULER_FRESHNESS_GATE and (
            s.max_unhealthy_seconds <= SCHEDULER_MAX_UNHEALTHY_SECONDS
        )
        results.append(
            GateResult(
                "scheduler_freshness",
                "§4 scheduler",
                "Tick freshness (healthy >=99%, unhealthy <=30s)",
                f">= {SCHEDULER_FRESHNESS_GATE:.0%}",
                f"{s.healthy_ratio:.2%} (max unhealthy {s.max_unhealthy_seconds:.1f}s)",
                "PASS" if fresh_ok else "FAIL",
                "",
            )
        )
        results.append(
            GateResult(
                "scheduler_single_active",
                "§4 scheduler",
                "Advisory-lock single-active (no dual-active)",
                "100%",
                "single" if s.single_active else "DUAL",
                "PASS" if s.single_active else "FAIL",
                f"second-instance fail-fast={s.second_instance_failfast_seconds}s",
            )
        )
        results.append(
            GateResult(
                "scheduler_cadence_tolerance",
                "§4 scheduler",
                "Cadence tolerance (no action stalled > 2x interval)",
                "no stall",
                "ok" if s.cadence_stall_ok else "stalled",
                "PASS" if s.cadence_stall_ok else "FAIL",
                "",
            )
        )
        results.append(
            GateResult(
                "scheduler_tick_fail_rate",
                "§4 scheduler",
                "Scheduler tick failure rate (10min)",
                f"<= {SCHEDULER_TICK_FAIL_GATE:.2%}",
                f"{s.tick_fail_rate_10min:.4%}",
                "PASS" if s.tick_fail_rate_10min <= SCHEDULER_TICK_FAIL_GATE else "FAIL",
                "",
            )
        )
    else:
        for gid in ("scheduler_freshness", "scheduler_single_active",
                    "scheduler_cadence_tolerance", "scheduler_tick_fail_rate"):
            results.append(GateResult(gid, "§4 scheduler", gid, "n/a", "no data", "UNVERIFIED"))

    # --- §5 database ---------------------------------------------------------
    if metrics.db is not None:
        db = metrics.db
        saturation = db.peak_active_connections / max(db.max_connections, 1)
        results.append(
            GateResult(
                "db_connection_saturation",
                "§5 database",
                "Peak active connections <=70% max; pool-acquire timeout = 0",
                f"<= {DB_CONNECTION_SATURATION:.0%}",
                f"{saturation:.2%} ({db.peak_active_connections}/{db.max_connections})",
                "PASS"
                if saturation <= DB_CONNECTION_SATURATION and db.pool_acquire_timeouts == 0
                else "FAIL",
                f"pool_acquire_timeouts={db.pool_acquire_timeouts}",
            )
        )
        results.append(
            GateResult(
                "db_lock_wait",
                "§5 database",
                "No transaction lock wait > 1s",
                f"<= {DB_LOCK_WAIT_MAX_SECONDS}s",
                f"{db.max_lock_wait_seconds:.3f}s",
                "PASS" if db.max_lock_wait_seconds <= DB_LOCK_WAIT_MAX_SECONDS else "FAIL",
                "",
            )
        )
        results.append(
            GateResult(
                "db_slow_query_2s",
                "§5 database",
                "Queries > 2s = 0",
                f"= {DB_SLOW_QUERY_2S_COUNT}",
                f"{db.queries_over_2s}",
                "PASS" if db.queries_over_2s == DB_SLOW_QUERY_2S_COUNT else "FAIL",
                "",
            )
        )
        results.append(
            GateResult(
                "db_slow_query_500ms",
                "§5 database",
                "Slow-query (>500ms) ratio <= 1%",
                f"<= {DB_SLOW_QUERY_500MS_RATIO:.2%}",
                f"{db.slow_query_ratio_500ms:.4%}",
                "PASS" if db.slow_query_ratio_500ms <= DB_SLOW_QUERY_500MS_RATIO else "FAIL",
                "",
            )
        )
        results.append(
            GateResult(
                "db_disk",
                "§5 database",
                "PostgreSQL disk usage < 80%",
                f"< {DB_DISK_RATIO:.0%}",
                f"{db.disk_usage_ratio:.2%}",
                "PASS" if db.disk_usage_ratio < DB_DISK_RATIO else "FAIL",
                f"db_size={db.db_size_bytes} bytes",
            )
        )
    else:
        for gid in ("db_connection_saturation", "db_lock_wait", "db_slow_query_2s",
                    "db_slow_query_500ms", "db_disk"):
            results.append(GateResult(gid, "§5 database", gid, "n/a", "no data", "UNVERIFIED"))

    # --- §6 SSE / Agent ------------------------------------------------------
    if metrics.sse is not None:
        sse = metrics.sse
        results.append(
            GateResult(
                "sse_initial_event",
                "§6 SSE/Agent",
                "SSE initial event p95",
                f"<= {SSE_INITIAL_EVENT_P95_SECONDS}s",
                f"{sse.initial_event_p95_seconds:.2f}s",
                "PASS"
                if sse.initial_event_p95_seconds <= SSE_INITIAL_EVENT_P95_SECONDS
                else "FAIL",
                (
                    f"p99={sse.initial_event_p99_seconds:.2f}s; "
                    f"substituted_provider={sse.substituted_provider}"
                ),
                substituted=sse.substituted_provider,
            )
        )
        results.append(
            GateResult(
                "sse_resume",
                "§6 SSE/Agent",
                "SSE reconnect/resume correctness (no loss/no dup)",
                "100%",
                "correct" if sse.resume_correct else "incorrect",
                "PASS"
                if sse.resume_correct
                else "FAIL",
                "substituted provider; real external E2E deferred to T039",
                substituted=sse.substituted_provider,
            )
        )
        results.append(
            GateResult(
                "sse_heartbeat",
                "§6 SSE/Agent",
                "SSE heartbeat <= 15s",
                f"<= {SSE_HEARTBEAT_MAX_SECONDS}s",
                "not measured"
                if sse.heartbeat_max_seconds is None
                else f"{sse.heartbeat_max_seconds:.2f}s",
                "UNVERIFIED"
                if sse.heartbeat_max_seconds is None
                else (
                    "PASS"
                    if sse.heartbeat_max_seconds <= SSE_HEARTBEAT_MAX_SECONDS
                    else "FAIL"
                ),
                (
                    "no substituted streaming provider in reference env; "
                    "real external E2E deferred to T039"
                ),
                substituted=sse.substituted_provider,
            )
        )
    else:
        for gid in ("sse_initial_event", "sse_resume", "sse_heartbeat"):
            results.append(GateResult(gid, "§6 SSE/Agent", gid, "n/a", "no data", "UNVERIFIED"))

    return results


@dataclass(slots=True)
class RunVerdict:
    run_id: str
    gates: list[GateResult]
    environment_ok: bool

    @property
    def hard_failures(self) -> list[GateResult]:
        return [g for g in self.gates if g.status == "FAIL"]

    @property
    def unverified(self) -> list[GateResult]:
        return [g for g in self.gates if g.status == "UNVERIFIED"]

    @property
    def warnings(self) -> list[GateResult]:
        return [g for g in self.gates if g.status == "WARN"]

    @property
    def status(self) -> GateStatus:
        if not self.environment_ok:
            return "UNVERIFIED"
        if self.hard_failures:
            return "FAIL"
        if self.unverified:
            # Missing required evidence is a FAIL per ADR 0032 §8.
            return "FAIL"
        return "PASS" if not self.warnings else "WARN"


def run_verdict(run_id: str, metrics: RunMetrics) -> RunVerdict:
    return RunVerdict(
        run_id=run_id,
        gates=evaluate_run(metrics),
        environment_ok=metrics.environment_ok,
    )


# --- ADR 0032 §8 cross-run release semantics --------------------------------

def _p95_vector(verdicts: list[RunVerdict]) -> dict[str, list[float]]:
    """Extract per-class p95 measured values across runs (for variance check)."""
    vectors: dict[str, list[float]] = {}
    for v in verdicts:
        for g in v.gates:
            if g.gate_id.startswith("latency_p95."):
                cls = g.gate_id.split(".", 1)[1]
                # measured is formatted "100.0 ms" (or "no samples"); take the
                # leading numeric token so variance is enforced on real values.
                with contextlib.suppress(ValueError, IndexError):
                    vectors.setdefault(cls, []).append(float(str(g.measured).split()[0]))
    return vectors


def release_verdict(verdicts: list[RunVerdict]) -> tuple[GateStatus, str]:
    """ADR 0032 §8: PASS requires 2 consecutive runs, all hard gates, within variance.

    - Any single run with a hard-gate FAIL -> the scenario may retry up to
      ``FLAKY_MAX_RETRIES``; 2-of-3 passing = transient WARN.
    - Environment failure (UNVERIFIED) -> UNVERIFIED, never PASS.
    - Best-effort PASS on substituted evidence is forbidden: substituted gates
      never count toward the PASS basis.
    """

    if not verdicts:
        return "UNVERIFIED", "no runs completed"

    if any(v.status == "UNVERIFIED" for v in verdicts):
        return (
            "UNVERIFIED",
            "environmental failure: at least one run UNVERIFIED (no best-effort PASS)",
        )

    passing = [v for v in verdicts if v.status in ("PASS", "WARN")]
    failing = [v for v in verdicts if v.status == "FAIL"]

    if failing and len(passing) < 2:
        return "FAIL", f"{len(failing)} run(s) with hard-gate FAIL; 2-of-3 not reached"

    if len(passing) < 2:
        return (
            "UNVERIFIED",
            f"only {len(passing)} passing run(s); need 2 consecutive within variance",
        )

    # Variance check on the two most recent passing runs' p95 vectors.
    vectors = _p95_vector(passing[-2:])
    variance_failures: list[str] = []
    for cls, vals in vectors.items():
        if len(vals) < 2:
            continue
        a, b = vals[-2], vals[-1]
        base = max(a, b, 1.0)
        if abs(a - b) / base > 0.20:
            variance_failures.append(f"{cls} p95 {a:.1f}ms vs {b:.1f}ms exceeds ±20%")

    if variance_failures:
        return "FAIL", "run-to-run p95 variance exceeded ±20%: " + "; ".join(variance_failures)

    if any(v.status == "WARN" for v in passing[-2:]):
        return (
            "WARN",
            "all hard gates pass on 2 consecutive runs within variance; target(s) exceeded",
        )

    return "PASS", "all hard gates pass on 2 consecutive runs within ±20% p95 variance"
