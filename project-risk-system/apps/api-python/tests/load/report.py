"""Artifact writer for the T038 load harness (ADR 0032 §7).

Emits the three required artifact tiers into ``artifacts/load/**``:

* **raw** — per-class latency samples + status codes (``raw_<class>.jsonl``).
* **summary** — materialized :class:`~load.stats.ClassSummary` and the
  §2-§6 metric snapshots (``summary.json``).
* **machine-readable verdict** — per-run ``result.json`` and a top-level
  cross-run ``result.json`` with the ADR 0032 §8 release verdict.

All artifacts are deterministic given the run metrics (no timestamps in the
machine-readable body except the run id provided by the orchestrator).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .gates import RunVerdict, release_verdict
from .stats import ClassSummary


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    path.write_text(text, encoding="utf-8")


def write_run_artifacts(
    run_dir: Path,
    run_id: str,
    config: object,
    class_summaries: dict[str, ClassSummary],
    metrics: object,
    verdict: RunVerdict,
    load_meta: dict[str, object],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)

    # --- raw per-class samples ---
    for name, summary in class_summaries.items():
        raw_path = run_dir / f"raw_{name}.json"
        _write_json(
            raw_path,
            {
                "endpoint_class": name,
                "total": summary.total,
                "success": summary.success,
                "error_5xx": summary.error_5xx,
                "substituted_5xx": summary.substituted_5xx,
                "p50_ms": summary.p50_ms,
                "p95_ms": summary.p95_ms,
                "p99_ms": summary.p99_ms,
                "error_5xx_ratio": summary.error_5xx_ratio,
            },
        )

    # --- summary (§1-§6 metric snapshot) ---
    summary_payload: dict[str, object] = {
        "run_id": run_id,
        "config": _config_dict(config),
        "load": load_meta,
        "endpoint_classes": {n: asdict(s) for n, s in class_summaries.items()},
        "metrics": _metrics_dict(metrics),
    }
    _write_json(run_dir / "summary.json", summary_payload)

    # --- per-run machine-readable verdict ---
    gates_payload = [g.to_dict() for g in verdict.gates]
    _write_json(
        run_dir / "result.json",
        {
            "run_id": run_id,
            "verdict": verdict.status,
            "environment_ok": verdict.environment_ok,
            "hard_failures": [g.gate_id for g in verdict.hard_failures],
            "unverified": [g.gate_id for g in verdict.unverified],
            "warnings": [g.gate_id for g in verdict.warnings],
            "gates": gates_payload,
        },
    )


def write_release_artifact(
    base_dir: Path,
    verdicts: list[RunVerdict],
) -> Path:
    status, detail = release_verdict(verdicts)
    _write_json(
        base_dir / "result.json",
        {
            "task": "T038",
            "adr": "0032",
            "release_verdict": status,
            "detail": detail,
            "runs": [
                {
                    "run_id": v.run_id,
                    "verdict": v.status,
                    "hard_failures": [g.gate_id for g in v.hard_failures],
                    "unverified": [g.gate_id for g in v.unverified],
                    "warnings": [g.gate_id for g in v.warnings],
                }
                for v in verdicts
            ],
        },
    )
    return base_dir / "result.json"


def _config_dict(config: object) -> dict[str, object]:
    try:
        keys = ("vu_count", "warmup_seconds", "measurement_seconds", "seed")
        return {k: getattr(config, k) for k in keys}
    except AttributeError:
        return {}


def _metrics_dict(metrics: object) -> dict[str, object]:
    out: dict[str, object] = {}
    for attr in ("db", "scheduler", "worker_queue", "tasks", "sse"):
        val = getattr(metrics, attr, None)
        if val is not None:
            out[attr] = asdict(val)
    out["environment_ok"] = getattr(metrics, "environment_ok", True)
    out["environment_detail"] = getattr(metrics, "environment_detail", "")
    return out
