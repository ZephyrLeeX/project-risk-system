"""T038 capacity & resilience load/capacity acceptance harness.

ADR 0032 (DG-05 resolution) approves the numeric thresholds and release-gate
semantics this package implements. The harness is **measurement-only**: it seeds
a reproducible baseline dataset, drives 50 concurrent authenticated virtual
users against the T035 production Compose topology, collects API/DB/worker/
scheduler/SSE evidence, and emits a machine-readable PASS/FAIL artifact into
``artifacts/load/**``.

It never edits production code, schema, index or migration. Any threshold
failure is recorded as a ``FAIL finding`` returned to the owning task; T038 does
not implement production fixes (ADR 0032 §0/§10).

Write-set: ``apps/api-python/tests/load/**`` + ``artifacts/load/**`` only.
"""

from __future__ import annotations
