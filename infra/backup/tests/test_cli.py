"""CLI operator-path wiring (ADR 0031 §10, §12).

These do not duplicate the real PostgreSQL drill (``test_backup_restore_pg.py``)
or the orchestrator unit/restore negatives. They guard the CLI argument →
``PgDumper`` wiring that the drill (which calls ``run_backup``/``run_restore``
directly) does not exercise: specifically that ``cmd_restore`` builds its
``PgDumper`` from ``--target-dsn`` (not ``--dsn``, which the restore subparser
does not define) and ``cmd_backup`` from ``--dsn``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from risk_backup.backup import BackupOutcome
from risk_backup.cli import main
from risk_backup.manifest import BackupStatus
from risk_backup.restore import RestoreOutcome


def _write_kek(path: Path) -> Path:
    import base64
    import secrets

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.b64encode(secrets.token_bytes(32)).decode("ascii") + "\n", "ascii")
    path.chmod(0o400)
    return path


def test_cli_backup_builds_pg_dumper_from_source_dsn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kek = _write_kek(tmp_path / "keys" / "kek_v1")
    captured: dict[str, Any] = {}

    def fake_run_backup(req: Any) -> BackupOutcome:
        captured["req"] = req
        return BackupOutcome(status=BackupStatus.USABLE, artifact_path=Path(req.output_path))

    monkeypatch.setattr("risk_backup.cli.run_backup", fake_run_backup)

    rc = main(
        [
            "backup",
            "--type",
            "daily",
            "--dsn",
            "postgresql://project_risk:pw@host:5432/srcdb",
            "--pg-socket-dir",
            "/var/run/postgresql",
            "--pg-user",
            "project_risk",
            "--pg-db",
            "srcdb",
            "--storage-root",
            str(tmp_path / "storage"),
            "--output",
            str(tmp_path / "out" / "b.rpbk"),
            "--temp-dir",
            str(tmp_path / "tmp"),
            "--kek-version",
            "v1",
            "--kek-file",
            f"v1={kek}",
            "--quiesce",
            "none",
        ]
    )

    assert rc == 0
    req = captured["req"]
    assert req.dsn.endswith("/srcdb")
    # The dumper targets the source DB (socket connection inside the container).
    assert req.pg_dumper.connection.database == "srcdb"
    assert req.pg_dumper.connection.socket_dir == "/var/run/postgresql"


def test_cli_restore_builds_pg_dumper_from_target_dsn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kek = _write_kek(tmp_path / "keys" / "kek_v1")
    artifact = tmp_path / "artifact.rpbk"
    artifact.write_bytes(b"dummy")  # run_restore is faked; the file is never opened.
    captured: dict[str, Any] = {}

    def fake_run_restore(req: Any) -> RestoreOutcome:
        captured["req"] = req
        return RestoreOutcome()  # ok=False (no manifest) -> CLI exit 1

    monkeypatch.setattr("risk_backup.cli.run_restore", fake_run_restore)

    rc = main(
        [
            "restore",
            "--artifact",
            str(artifact),
            "--target-dsn",
            "postgresql://project_risk:pw@host:5432/tgtdb",
            "--pg-socket-dir",
            "/var/run/postgresql",
            "--pg-user",
            "project_risk",
            "--pg-db",
            "tgtdb",
            "--target-storage-root",
            str(tmp_path / "restore"),
            "--temp-dir",
            str(tmp_path / "plain"),
            "--kek-version",
            "v1",
            "--kek-file",
            f"v1={kek}",
        ]
    )

    assert rc == 1  # restore did not succeed (faked empty outcome)
    req = captured["req"]
    assert req.target_dsn.endswith("/tgtdb")
    # The dumper must target the isolated restore target DB, not a (absent) --dsn.
    assert req.pg_dumper.connection.database == "tgtdb"
