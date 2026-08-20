"""ADR 0032 load/capacity acceptance configuration.

All numeric thresholds are transcribed verbatim from ADR 0032 §§1-8 so the gate
evaluator and the report agree on a single source of truth. Tunable run
parameters (concurrency, warmup, measurement window, sample targets) carry the
ADR 0032 §1/§9 minimums as their defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# --- ADR 0032 §1 API latency gates (p95 hard gate, ms) ------------------------
FAST_READ_P95_MS: Final = 500
MUTATION_P95_MS: Final = 800
ADMIN_OVERVIEW_P95_MS: Final = 2_000
ASYNC_DISPATCH_P95_MS: Final = 1_000
AUTH_P95_MS: Final = 1_000

# p50 targets (WARN reference, ms) and p99 report ceilings (ms).
FAST_READ_P50_MS: Final = 150
FAST_READ_P99_MS: Final = 1_000
MUTATION_P50_MS: Final = 300
MUTATION_P99_MS: Final = 1_500
ADMIN_OVERVIEW_P50_MS: Final = 800
ADMIN_OVERVIEW_P99_MS: Final = 3_000
ASYNC_DISPATCH_P50_MS: Final = 300
ASYNC_DISPATCH_P99_MS: Final = 1_500
AUTH_P50_MS: Final = 400
AUTH_P99_MS: Final = 1_500

# --- ADR 0032 §2 error-rate gates ---------------------------------------------
HTTP_5XX_GATE: Final = 0.005  # 0.5%
TASK_FAILED_GATE: Final = 0.01  # 1% (excludes external AI/IMAP substitute failures)
TASK_RETRY_GATE: Final = 0.05  # 5%
SCHEDULER_TICK_FAIL_GATE: Final = 0.01  # 1% over 10min window
SSE_UNEXPECTED_DISCONNECT_GATE: Final = 1.0  # <=1 per connection per 5min
SSE_RESUME_CORRECTNESS_GATE: Final = 1.0  # 100%

# --- ADR 0032 §3 worker/queue gates ------------------------------------------
QUEUE_AGE_P95_SECONDS: Final = 15.0
QUEUE_AGE_P99_SECONDS: Final = 30.0
OUTBOX_UNPUBLISHED_AGE_SECONDS: Final = 30.0
RETRY_BACKLOG_GATE: Final = 50
WORKER_AVAILABILITY_GATE: Final = 1.0  # >=1 node, 100% of window

# --- ADR 0032 §4 scheduler gates ---------------------------------------------
SCHEDULER_FRESHNESS_GATE: Final = 0.99  # healthy >=99% of window
SCHEDULER_MAX_UNHEALTHY_SECONDS: Final = 30.0
SCHEDULER_CADENCE_TOLERANCE: Final = 2.0  # action must not stall > 2x interval

# --- ADR 0032 §5 database gates ----------------------------------------------
DB_CONNECTION_SATURATION: Final = 0.70  # <=70% of max_connections
DB_LOCK_WAIT_MAX_SECONDS: Final = 1.0
DB_SLOW_QUERY_2S_COUNT: Final = 0
DB_SLOW_QUERY_500MS_RATIO: Final = 0.01  # <=1%
DB_DISK_RATIO: Final = 0.80  # <80%

# --- ADR 0032 §6 SSE/Agent gates ---------------------------------------------
SSE_INITIAL_EVENT_P95_SECONDS: Final = 2.0
SSE_INITIAL_EVENT_P99_SECONDS: Final = 3.0
SSE_HEARTBEAT_MAX_SECONDS: Final = 15.0
SSE_PROVIDER_FAILBACK_WITHIN_SECONDS: Final = 900.0  # task timeout

# --- ADR 0032 §8 release-gate / §9 methodology -------------------------------
VARIANCE_P95_RATIO: Final = 0.20  # run-to-run p95 within ±20%
VARIANCE_ERROR_RATE_PP: Final = 0.003  # run-to-run error-rate delta <=0.3pp
FLAKY_MAX_RETRIES: Final = 2  # at most 2 extra runs; 2-of-3 decides

# ADR 0009 capacity baseline.
BASELINE_USERS: Final = 300
BASELINE_PROJECTS: Final = 5_000
BASELINE_WEEKLY_MAIL: Final = 1_000


@dataclass(frozen=True, slots=True)
class EndpointClassThreshold:
    """Latency thresholds for one ADR 0032 §1 endpoint class."""

    name: str
    p50_ms: int
    p95_ms: int  # hard gate
    p99_ms: int  # report ceiling (WARN when exceeded)


ENDPOINT_CLASSES: Final = (
    EndpointClassThreshold("fast_read", FAST_READ_P50_MS, FAST_READ_P95_MS, FAST_READ_P99_MS),
    EndpointClassThreshold("mutation", MUTATION_P50_MS, MUTATION_P95_MS, MUTATION_P99_MS),
    EndpointClassThreshold(
        "admin_overview", ADMIN_OVERVIEW_P50_MS, ADMIN_OVERVIEW_P95_MS, ADMIN_OVERVIEW_P99_MS
    ),
    EndpointClassThreshold(
        "async_dispatch", ASYNC_DISPATCH_P50_MS, ASYNC_DISPATCH_P95_MS, ASYNC_DISPATCH_P99_MS
    ),
    EndpointClassThreshold("auth", AUTH_P50_MS, AUTH_P95_MS, AUTH_P99_MS),
)

# Minimum successful samples per class: p95 gate needs >=500, p99 report >=1000.
MIN_SAMPLES_P95: Final = 500
MIN_SAMPLES_P99: Final = 1_000


@dataclass(frozen=True, slots=True)
class LoadConfig:
    """Tunable run parameters (ADR 0032 §1/§9 minimums as defaults)."""

    # 50 concurrent authenticated virtual users (ADR 0032 §1).
    vu_count: int = 50
    # VU allocation across roles (must sum to vu_count).
    vu_system_admin: int = 6
    vu_risk_admin: int = 12
    vu_project_manager: int = 20
    vu_viewer_auditor: int = 12
    # warmup >= 30s (samples discarded), measurement window >= 60s.
    warmup_seconds: float = 30.0
    measurement_seconds: float = 150.0
    # Hard cap so a stuck run cannot loop forever; min window still enforced.
    max_measurement_seconds: float = 300.0
    # Per-class sample targets.
    min_samples_p95: int = MIN_SAMPLES_P95
    min_samples_p99: int = MIN_SAMPLES_P99
    # Request pacing: max in-flight requests per VU (keeps a single uvicorn honest).
    max_inflight_per_vu: int = 1
    # RNG seed for reproducible fixtures (ADR 0032 §9 reproducibility).
    seed: int = 20260815
    # Reproducibility variance (ADR 0032 §9).
    variance_p95_ratio: float = VARIANCE_P95_RATIO
    variance_error_rate_pp: float = VARIANCE_ERROR_RATE_PP
    flaky_max_retries: int = FLAKY_MAX_RETRIES

    def validate(self) -> None:
        allocated = (
            self.vu_system_admin
            + self.vu_risk_admin
            + self.vu_project_manager
            + self.vu_viewer_auditor
        )
        if allocated != self.vu_count:
            raise ValueError(f"VU allocation {allocated} != vu_count {self.vu_count}")
        if self.warmup_seconds < 30.0:
            raise ValueError("warmup must be >= 30s (ADR 0032 §1)")
        if self.measurement_seconds < 60.0:
            raise ValueError("measurement window must be >= 60s (ADR 0032 §1)")


DEFAULT_CONFIG: Final = LoadConfig()


# Endpoint class names mapped to the ADR 0032 §1 table for report formatting.
CLASS_LABELS: Final = {
    "fast_read": "Fast read",
    "mutation": "Mutation",
    "admin_overview": "Admin overview",
    "async_dispatch": "Async dispatch",
    "auth": "Auth",
}
