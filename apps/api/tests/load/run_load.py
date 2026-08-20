"""T038 load-acceptance orchestrator (ADR 0032 §§7-9).

Single entry point that (optionally) seeds the reproducible baseline dataset,
then drives N measurement runs of the 50-VU fleet against the T035 Compose
proxy, collects §2-§6 evidence, evaluates the ADR 0032 hard gates, and writes
raw + summary + machine-readable verdict artifacts into ``artifacts/load/**``.

Run as a module so relative imports resolve::

    PYTHONPATH=tests uv run --frozen python -m load.run_load \
        --env-file ../.env --artifacts-dir ../../artifacts/load --runs 2

It never edits production code, schema, index or migration.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import cast

from .collectors import DbCollector, InfraCollector, _connect_dict, probe_sse
from .config import DEFAULT_CONFIG, LoadConfig
from .gates import RunMetrics, RunVerdict, run_verdict
from .generator import LOAD_PASSWORD, generate_dataset
from .report import write_release_artifact, write_run_artifacts
from .scenarios import drive_load
from .stats import LatencySamples, summarise

log = logging.getLogger("t038.load")


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


async def _seed_if_needed(database_url: str, env: dict[str, str], force: bool) -> dict[str, object]:
    """Seed the baseline dataset; return dataset metadata (conversation id etc)."""

    from risk_platform.composition import load_cipher
    from risk_platform.db import create_database_engine, create_session_factory, transaction
    from risk_platform.db import database_url as resolve_url

    os.environ.update(env)
    cipher = load_cipher()
    if cipher is None:
        raise RuntimeError("DATA_ENCRYPTION_KEY not configured; cannot seed mailbox auth codes")

    marker = Path(os.environ.get("ARTIFACTS_DIR", "artifacts/load")) / ".dataset.json"
    if marker.exists() and not force:
        import json

        return cast(dict[str, object], json.loads(marker.read_text(encoding="utf-8")))

    url = resolve_url() if os.environ.get("DATABASE_URL") else database_url
    engine = create_database_engine(url, pool_pre_ping=True)
    factory = create_session_factory(engine)
    async with transaction(factory) as session:
        dataset = await generate_dataset(session, cipher, seed=DEFAULT_CONFIG.seed)
    await engine.dispose()

    import json

    meta = {
        "conversation_id": str(dataset.conversation_ids[0]),
        "project_count": len(dataset.project_ids),
        "risk_count": len(dataset.risk_ids),
        "todo_count": len(dataset.todo_ids),
        "conversation_count": len(dataset.conversation_ids),
        "seed": DEFAULT_CONFIG.seed,
    }
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("seeded dataset: %s", meta)
    return meta


async def _run_once(
    run_id: str,
    config: LoadConfig,
    base_url: str,
    database_url: str,
    artifacts_dir: Path,
    dataset_meta: dict[str, object],
    shim: _DatasetShim,
) -> tuple[RunVerdict, RunMetrics]:
    """Execute one measurement run and write its artifacts; return (verdict, metrics)."""

    samples = {cls: LatencySamples(cls) for cls in
               ("fast_read", "mutation", "admin_overview", "async_dispatch", "auth")}
    db_collector = DbCollector(database_url)
    infra = InfraCollector()
    await db_collector.start()
    await infra.start()

    retry_backlog_start = await db_collector.retry_backlog_now()
    window_started_at = time.time()

    load_result = await drive_load(config, shim, base_url, LOAD_PASSWORD, samples)

    retry_backlog_end = await db_collector.retry_backlog_now()
    transport_errors = sum(
        1 for s in samples.values() for status in s.status_counts if status == 599
    )
    db_metrics, task_metrics, wq_metrics = await db_collector.snapshot(
        window_started_at, transport_errors
    )
    await db_collector.stop()
    await infra.stop()

    wq_metrics.worker_availability_ratio = infra.worker_availability()
    wq_metrics.retry_backlog_monotonic = retry_backlog_end <= retry_backlog_start
    scheduler_metrics = infra.scheduler_metrics()

    # Seeded username format is ``load-{role.lower()}-{idx:04d}``; RISK_ADMIN
    # lower-cases to ``risk_admin`` (underscore), not ``risk-admin``. RA holds
    # agent.use + ALL data scope, so it can open the seeded conversation's SSE
    # stream. A hyphen here would 401 the probe login and corrupt SSE evidence.
    sse_username = "load-risk_admin-0000"
    sse_metrics = await probe_sse(
        base_url, sse_username, LOAD_PASSWORD, str(dataset_meta["conversation_id"]),
        samples=15,
    )

    class_summaries = summarise(samples)
    metrics = RunMetrics(
        classes=class_summaries,
        db=db_metrics,
        scheduler=scheduler_metrics,
        worker_queue=wq_metrics,
        tasks=task_metrics,
        sse=sse_metrics,
        environment_ok=True,
    )
    verdict = run_verdict(run_id, metrics)

    load_meta = {
        "vu_request_counts": load_result.vu_request_counts,
        "total_requests": sum(load_result.vu_request_counts),
        "measurement_seconds_actual": load_result.measurement_end - load_result.measurement_start,
        "retry_backlog_start": retry_backlog_start,
        "retry_backlog_end": retry_backlog_end,
    }
    write_run_artifacts(
        artifacts_dir / run_id, run_id, config, class_summaries, metrics, verdict, load_meta
    )
    log.info("run %s verdict=%s hard_failures=%d unverified=%d",
             run_id, verdict.status, len(verdict.hard_failures), len(verdict.unverified))
    return verdict, metrics


class _DatasetShim:
    """Minimal attribute shim so drive_load can read seeded resource id pools."""

    def __init__(self, meta: dict[str, object]) -> None:
        self._meta = meta
        # Resource id pools are not persisted (too large); drive_load only needs
        # todo_ids for mutations. We re-read a sample from the DB lazily.
        self.todo_ids: list[uuid.UUID] = []
        self.risk_ids: list[uuid.UUID] = []
        self.project_ids: list[uuid.UUID] = []
        self.vu_usernames: dict[str, str] = {}

    async def load_pools(self, database_url: str) -> None:
        from .collectors import _conninfo

        conninfo = _conninfo(database_url)
        async with await _connect_dict(conninfo) as conn, conn.cursor() as cur:
            await cur.execute("SELECT id FROM action_items LIMIT 2000")
            self.todo_ids = [r["id"] for r in await cur.fetchall()]
            await cur.execute("SELECT id FROM risks LIMIT 1000")
            self.risk_ids = [r["id"] for r in await cur.fetchall()]


async def _async_main(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    env = _load_env(args.env_file)
    os.environ.update(env)

    database_url = args.database_url or os.environ["DATABASE_URL"]
    # Normalize for psycopg collectors (strip the +psycopg dialect suffix).
    base_url = args.base_url

    artifacts_dir = Path(args.artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ARTIFACTS_DIR"] = str(artifacts_dir)

    dataset_meta = await _seed_if_needed(database_url, env, force=args.seed)

    config = LoadConfig()
    config.validate()

    shim = _DatasetShim(dataset_meta)
    await shim.load_pools(database_url)
    verdicts: list[RunVerdict] = []
    for i in range(args.runs):
        run_id = f"run-{i + 1}"
        verdict, _metrics = await _run_once(
            run_id, config, base_url, database_url, artifacts_dir, dataset_meta, shim
        )
        verdicts.append(verdict)
        # ADR 0032 §8: stop early on environment failure (UNVERIFIED, never PASS).
        if verdict.status == "UNVERIFIED":
            log.warning("run %s UNVERIFIED (environment); stopping per §8", run_id)
            break

    release_path = write_release_artifact(artifacts_dir, verdicts)
    log.info("release verdict written to %s", release_path)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="T038 load acceptance orchestrator")
    parser.add_argument("--env-file", type=Path, default=Path("../.env"))
    parser.add_argument("--base-url", default="https://localhost:8443")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--artifacts-dir", default="../../artifacts/load")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--seed", action="store_true", help="force re-seed the baseline dataset")
    args = parser.parse_args()
    sys.exit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
