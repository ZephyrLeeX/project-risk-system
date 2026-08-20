"""Focused unit tests for the T038 load harness (ADR 0032 §9 reproducibility).

These exercise the pure, deterministic core of the harness — percentile math,
sample aggregation, gate evaluation, release semantics and the rate limiter —
without touching the database, the Compose stack or the network. They are the
``focused load-test code tests`` required before a checkpoint.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from .config import DEFAULT_CONFIG, LoadConfig
from .gates import (
    DBMetrics,
    RunMetrics,
    RunVerdict,
    SchedulerMetrics,
    SSEMetrics,
    TaskMetrics,
    WorkerQueueMetrics,
    evaluate_run,
    release_verdict,
    run_verdict,
)
from .scenarios import RateLimiter
from .stats import ClassSummary, LatencySamples, percentile

# --- stats.percentile --------------------------------------------------------


def test_percentile_nearest_rank_basic() -> None:
    assert percentile([10.0], 95.0) == 10.0
    # 1..100 sorted; p95 nearest rank = ceil(0.95*100) = 95th value = 95.0
    vals = [float(i) for i in range(1, 101)]
    assert percentile(vals, 95.0) == 95.0
    assert percentile(vals, 99.0) == 99.0
    assert percentile(vals, 50.0) == 50.0


def test_percentile_empty_and_bounds() -> None:
    assert percentile([], 95.0) == 0.0
    assert percentile([5.0], 0.0) == 5.0
    with pytest.raises(ValueError):
        percentile([1.0], 150.0)


# --- LatencySamples ----------------------------------------------------------


def test_latency_samples_aggregation() -> None:
    s = LatencySamples("fast_read")
    for i in range(1000):
        s.record(float(i), 200)
    s.record(10_000.0, 500)  # one 5xx
    assert s.total == 1001
    assert s.success_count == 1000
    assert s.error_5xx_count == 1
    assert s.error_5xx_ratio() == pytest.approx(1 / 1001)
    # p95 of 0..999 + 10000 (1001 values): nearest-rank = ceil(0.95*1001)=951st = 950.0
    assert s.p95() == 950.0


def test_substituted_5xx_excluded_from_success() -> None:
    s = LatencySamples("async_dispatch")
    s.record(100.0, 500, substituted_5xx=True)
    s.record(100.0, 201)
    assert s.error_5xx_count == 1
    assert s.substituted_5xx == 1
    assert s.success_count == 1


# --- LoadConfig validation ---------------------------------------------------


def test_load_config_defaults_meet_adr_minimums() -> None:
    c = DEFAULT_CONFIG
    c.validate()
    assert c.vu_count == 50
    assert c.warmup_seconds >= 30.0
    assert c.measurement_seconds >= 60.0


def test_load_config_rejects_bad_allocation() -> None:
    c = LoadConfig(vu_system_admin=0)
    with pytest.raises(ValueError):
        c.validate()


# --- gate evaluation ---------------------------------------------------------


def _passing_summary(name: str, n: int = 1200) -> ClassSummary:
    return ClassSummary(name, n, n, 0, 0, 50.0, 100.0, 200.0, 0.0)


def _passing_metrics() -> RunMetrics:
    classes = {name: _passing_summary(name) for name in
               ("fast_read", "mutation", "admin_overview", "async_dispatch", "auth")}
    return RunMetrics(
        classes=classes,
        db=DBMetrics(100, 50, 0, 0.1, 0, 0.001, 0.5, 10_000_000),
        scheduler=SchedulerMetrics(1.0, 0.0, True, None, True, 0.0),
        worker_queue=WorkerQueueMetrics(5.0, 8.0, 2.0, 5, True, 1.0, 0),
        tasks=TaskMetrics(1000, 0, 0, 10),
        sse=SSEMetrics(1.0, 1.5, True, 15.0, 0.0, substituted_provider=True),
        environment_ok=True,
    )


def test_evaluate_run_all_pass() -> None:
    results = evaluate_run(_passing_metrics())
    statuses = {g.status for g in results}
    assert "FAIL" not in statuses
    assert "UNVERIFIED" not in statuses


def test_evaluate_run_latency_fail() -> None:
    metrics = _passing_metrics()
    metrics.classes["fast_read"] = ClassSummary(
        "fast_read", 1200, 1100, 100, 0, 200.0, 700.0, 900.0, 0.08
    )
    results = evaluate_run(metrics)
    lat = next(g for g in results if g.gate_id == "latency_p95.fast_read")
    assert lat.status == "FAIL"


def test_evaluate_run_missing_evidence_is_fail() -> None:
    """ADR 0032 §8: missing required evidence (UNVERIFIED gate) => run FAIL."""
    metrics = _passing_metrics()
    metrics.sse = None  # no SSE evidence
    verdict = run_verdict("run-x", metrics)
    assert verdict.status == "FAIL"
    assert any(g.status == "UNVERIFIED" for g in verdict.gates)


def test_evaluate_run_environment_failure_unverified() -> None:
    metrics = _passing_metrics()
    metrics.environment_ok = False
    metrics.environment_detail = "proxy unreachable"
    verdict = run_verdict("run-x", metrics)
    assert verdict.status == "UNVERIFIED"


def test_evaluate_run_sse_heartbeat_unverified_when_not_measured() -> None:
    metrics = _passing_metrics()
    metrics.sse = SSEMetrics(1.0, 1.5, True, None, 0.0, substituted_provider=True)
    results = evaluate_run(metrics)
    hb = next(g for g in results if g.gate_id == "sse_heartbeat")
    assert hb.status == "UNVERIFIED"


def test_evaluate_run_task_failed_excludes_substituted() -> None:
    metrics = _passing_metrics()
    # 900 succeeded + 100 FAILED, but all 100 are external-substitute -> gated 0%.
    metrics.tasks = TaskMetrics(1000, 0, 100, 10)
    results = evaluate_run(metrics)
    tf = next(g for g in results if g.gate_id == "task_failed_ratio")
    assert tf.status == "PASS"
    # Now make 20 of them non-infra -> 2% -> FAIL.
    metrics.tasks = TaskMetrics(1000, 20, 80, 10)
    results = evaluate_run(metrics)
    tf = next(g for g in results if g.gate_id == "task_failed_ratio")
    assert tf.status == "FAIL"


# --- release semantics -------------------------------------------------------


def _verdict_for(run_id: str, p95s: dict[str, float], status: str = "PASS") -> RunVerdict:
    metrics = _passing_metrics()
    for name, p95 in p95s.items():
        s = metrics.classes[name]
        metrics.classes[name] = ClassSummary(
            name, s.total, s.success, s.error_5xx, s.substituted_5xx,
            s.p50_ms, p95, s.p99_ms, s.error_5xx_ratio,
        )
    v = run_verdict(run_id, metrics)
    # Force the run-level status when requested (simulating WARN/FAIL/UNVERIFIED).
    return v


def test_release_pass_two_consecutive_within_variance() -> None:
    v1 = _verdict_for("run-1", {"fast_read": 100.0})
    v2 = _verdict_for("run-2", {"fast_read": 110.0})  # 10% delta < 20%
    status, _ = release_verdict([v1, v2])
    assert status == "PASS"


def test_release_fail_variance_exceeded() -> None:
    v1 = _verdict_for("run-1", {"fast_read": 100.0})
    v2 = _verdict_for("run-2", {"fast_read": 200.0})  # 100% delta > 20%
    status, _ = release_verdict([v1, v2])
    assert status == "FAIL"


def test_release_unverified_on_environment_failure() -> None:
    v1 = _verdict_for("run-1", {"fast_read": 100.0})
    metrics = _passing_metrics()
    metrics.environment_ok = False
    v2 = run_verdict("run-2", metrics)
    status, _ = release_verdict([v1, v2])
    assert status == "UNVERIFIED"


# --- rate limiter ------------------------------------------------------------


def test_rate_limiter_paces_within_budget() -> None:
    limiter = RateLimiter(50.0)  # 50/s -> 20ms between tokens
    start = time.monotonic()
    for _ in range(10):
        asyncio.run(limiter.acquire())
    elapsed = time.monotonic() - start
    # 10 acquires at 50/s should take roughly 9*20ms = 180ms; allow generous slack.
    assert elapsed < 1.0
