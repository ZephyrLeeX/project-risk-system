"""Latency sample aggregation and percentile math for the load harness.

Pure, deterministic, dependency-free so it is unit-testable independently of the
live stack.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


def percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Return the ``pct`` percentile (0-100) of an already-sorted sequence.

    Uses the "nearest rank" method consistent with ADR 0032 reporting: the p-th
    percentile is the value at rank ``ceil(p/100 * n)``. Empty input -> 0.0.
    """

    n = len(sorted_values)
    if n == 0:
        return 0.0
    if not 0 <= pct <= 100:
        raise ValueError("percentile must be in [0, 100]")
    if pct == 0:
        return float(sorted_values[0])
    rank = max(1, int(-(-pct / 100.0 * n // 1)))  # ceil(p/100 * n)
    rank = min(rank, n)
    return float(sorted_values[rank - 1])


@dataclass(slots=True)
class LatencySamples:
    """Accumulator for one endpoint class's latency samples (milliseconds)."""

    endpoint_class: str
    latencies_ms: list[float] = field(default_factory=list)
    status_counts: dict[int, int] = field(default_factory=dict)
    # Samples whose 5xx was caused by an external AI/IMAP substitute (excluded
    # from the hard gate per ADR 0032 §2, but recorded as substituted evidence).
    substituted_5xx: int = 0

    def record(self, latency_ms: float, status: int, *, substituted_5xx: bool = False) -> None:
        self.latencies_ms.append(latency_ms)
        self.status_counts[status] = self.status_counts.get(status, 0) + 1
        if substituted_5xx:
            self.substituted_5xx += 1

    @property
    def total(self) -> int:
        return len(self.latencies_ms)

    @property
    def success_count(self) -> int:
        return sum(c for s, c in self.status_counts.items() if 200 <= s < 300)

    @property
    def error_5xx_count(self) -> int:
        return sum(c for s, c in self.status_counts.items() if 500 <= s < 600)

    def p50(self) -> float:
        return percentile(sorted(self.latencies_ms), 50.0)

    def p95(self) -> float:
        return percentile(sorted(self.latencies_ms), 95.0)

    def p99(self) -> float:
        return percentile(sorted(self.latencies_ms), 99.0)

    def error_5xx_ratio(self) -> float:
        total = self.total
        return self.error_5xx_count / total if total else 0.0


@dataclass(slots=True)
class ClassSummary:
    """Materialized summary of one endpoint class for the report."""

    endpoint_class: str
    total: int
    success: int
    error_5xx: int
    substituted_5xx: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_5xx_ratio: float

    @classmethod
    def from_samples(cls, samples: LatencySamples) -> ClassSummary:
        return cls(
            endpoint_class=samples.endpoint_class,
            total=samples.total,
            success=samples.success_count,
            error_5xx=samples.error_5xx_count,
            substituted_5xx=samples.substituted_5xx,
            p50_ms=samples.p50(),
            p95_ms=samples.p95(),
            p99_ms=samples.p99(),
            error_5xx_ratio=samples.error_5xx_ratio(),
        )


def summarise(all_samples: dict[str, LatencySamples]) -> dict[str, ClassSummary]:
    return {name: ClassSummary.from_samples(s) for name, s in all_samples.items()}
