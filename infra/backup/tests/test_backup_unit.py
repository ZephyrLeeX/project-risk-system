"""Backup orchestrator unit tests (fakes; no real DB/docker).

Covers: USABLE success + plaintext cleanup, no plaintext/key leakage, partial
backup -> INCOMPLETE (pg_dump failure, files failure), quiesce failure -> fail
closed, cleanup-failure -> USABLE + SEVERE warning, unquiesce failure -> USABLE
+ warning.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from risk_backup import db as dbmod
from risk_backup.backup import BackupRequest, run_backup
from risk_backup.errors import BackupError, CleanupWarning
from risk_backup.keys import BackupKeyRing
from risk_backup.manifest import BackupStatus, BackupType
from risk_backup.pgdump import PgConnection, PgDumper
from risk_backup.quiesce import NoopQuiescer, Quiescer


class _FakeConn:
    def close(self) -> None:
        return None


class _FailingQuiescer(Quiescer):
    def __init__(self, *, fail_quiesce: bool = False, fail_unquiesce: bool = False) -> None:
        self._fail_quiesce = fail_quiesce
        self._fail_unquiesce = fail_unquiesce

    def quiesce(self) -> None:
        if self._fail_quiesce:
            raise BackupError("QUIESCE_FAILED")

    def unquiesce(self) -> None:
        if self._fail_unquiesce:
            raise BackupError("UNQUIESCE_FAILED")


def _patch_db(monkeypatch: pytest.MonkeyPatch, *, head: str = "20260812_0008") -> None:
    monkeypatch.setattr(dbmod, "connect", lambda dsn: _FakeConn())
    monkeypatch.setattr(dbmod, "alembic_head", lambda conn: head)
    monkeypatch.setattr(dbmod, "schema_hash", lambda conn: "s" * 64)
    monkeypatch.setattr(dbmod, "server_version", lambda conn: "16.4")


def _make_dumper() -> PgDumper:
    return PgDumper(connection=PgConnection(username="u", database="d", socket_dir="/tmp"))


def _storage_with_file(tmp_path: Path) -> Path:
    storage = tmp_path / "storage"
    (storage / "excel" / "batch-1").mkdir(parents=True)
    (storage / "excel" / "batch-1" / "source.xlsx").write_bytes(b"PLAINTEXT-FILE-MARKER-67890")
    return storage


def _request(
    tmp_path: Path, key_ring: BackupKeyRing, quiescer: Quiescer | None = None
) -> BackupRequest:
    return BackupRequest(
        backup_type=BackupType.DAILY,
        dsn="postgresql://u:p@h/d",
        pg_dumper=_make_dumper(),
        storage_root=_storage_with_file(tmp_path),
        output_path=tmp_path / "out" / "daily.rpbk",
        backup_key_ring=key_ring,
        quiescer=quiescer or NoopQuiescer(),
        temp_dir=tmp_path / "tmp",
        created_by="tester",
        trace_id="trace-1",
        chunk_max_bytes=4096,
    )


def test_successful_backup_is_usable_and_cleans_plaintext(
    tmp_path: Path, default_key_ring: BackupKeyRing, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db(monkeypatch)
    monkeypatch.setattr(
        PgDumper, "dump", lambda self, out: Path(out).write_bytes(b"PLAINTEXT-DUMP-MARKER-12345")
    )
    req = _request(tmp_path, default_key_ring)
    outcome = run_backup(req)
    assert outcome.status is BackupStatus.USABLE
    assert outcome.artifact_path is not None and outcome.artifact_path.exists()
    assert outcome.manifest is not None
    assert outcome.error is None
    # Plaintext temp dir is gone.
    assert not req.temp_dir.exists()


def test_no_plaintext_or_key_leakage_in_artifact(
    tmp_path: Path, default_key_ring: BackupKeyRing, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db(monkeypatch)
    monkeypatch.setattr(
        PgDumper, "dump", lambda self, out: Path(out).write_bytes(b"PLAINTEXT-DUMP-MARKER-12345")
    )
    req = _request(tmp_path, default_key_ring)
    outcome = run_backup(req)
    assert outcome.usable
    assert outcome.artifact_path is not None
    blob = outcome.artifact_path.read_bytes()
    # Plaintext component content must never appear in the artifact.
    assert b"PLAINTEXT-DUMP-MARKER-12345" not in blob
    assert b"PLAINTEXT-FILE-MARKER-67890" not in blob
    # The KEK material (raw bytes and its base64 file encoding) must never appear.
    for key in default_key_ring.key_ring.keys.values():
        assert key not in blob
        assert base64.b64encode(key) not in blob


def test_pg_dump_failure_is_incomplete_no_artifact(
    tmp_path: Path, default_key_ring: BackupKeyRing, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db(monkeypatch)

    def _fail(self: PgDumper, out: Path) -> None:
        raise BackupError("PG_DUMP_FAILED")

    monkeypatch.setattr(PgDumper, "dump", _fail)
    req = _request(tmp_path, default_key_ring)
    outcome = run_backup(req)
    assert outcome.status is BackupStatus.INCOMPLETE
    assert outcome.error is not None and outcome.error.code == "PG_DUMP_FAILED"
    assert not req.output_path.exists()
    assert not req.temp_dir.exists()  # plaintext cleaned on failure too


def test_files_failure_is_incomplete(
    tmp_path: Path, default_key_ring: BackupKeyRing, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db(monkeypatch)
    monkeypatch.setattr(PgDumper, "dump", lambda self, out: Path(out).write_bytes(b"dump"))
    # Point storage root at a non-existent dir to force FILES_ROOT_MISSING.
    req = BackupRequest(
        backup_type=BackupType.DAILY,
        dsn="postgresql://u:p@h/d",
        pg_dumper=_make_dumper(),
        storage_root=tmp_path / "missing-storage",
        output_path=tmp_path / "out" / "daily.rpbk",
        backup_key_ring=default_key_ring,
        quiescer=NoopQuiescer(),
        temp_dir=tmp_path / "tmp",
    )
    outcome = run_backup(req)
    assert outcome.status is BackupStatus.INCOMPLETE
    assert outcome.error is not None and outcome.error.code == "FILES_ROOT_MISSING"
    assert not req.output_path.exists()


def test_quiesce_failure_is_fail_closed_no_artifact(
    tmp_path: Path, default_key_ring: BackupKeyRing, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db(monkeypatch)
    monkeypatch.setattr(PgDumper, "dump", lambda self, out: Path(out).write_bytes(b"dump"))
    req = _request(tmp_path, default_key_ring, quiescer=_FailingQuiescer(fail_quiesce=True))
    outcome = run_backup(req)
    assert outcome.status is BackupStatus.INCOMPLETE
    assert outcome.error is not None and outcome.error.code == "QUIESCE_FAILED"
    assert not req.output_path.exists()


def test_cleanup_failure_keeps_usable_artifact_with_severe_warning(
    tmp_path: Path, default_key_ring: BackupKeyRing, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db(monkeypatch)
    monkeypatch.setattr(PgDumper, "dump", lambda self, out: Path(out).write_bytes(b"dump"))

    def _fail_cleanup(paths: list[Path]) -> None:
        raise CleanupWarning("PLAINTEXT_CLEANUP_FAILED")

    monkeypatch.setattr("risk_backup.backup.cleanup_plaintext", _fail_cleanup)
    req = _request(tmp_path, default_key_ring)
    outcome = run_backup(req)
    assert outcome.status is BackupStatus.USABLE
    assert outcome.artifact_path is not None and outcome.artifact_path.exists()
    assert outcome.cleanup_warning is not None
    assert outcome.cleanup_warning.code == "PLAINTEXT_CLEANUP_FAILED"


def test_unquiesce_failure_keeps_usable_artifact_with_warning(
    tmp_path: Path, default_key_ring: BackupKeyRing, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db(monkeypatch)
    monkeypatch.setattr(PgDumper, "dump", lambda self, out: Path(out).write_bytes(b"dump"))
    req = _request(tmp_path, default_key_ring, quiescer=_FailingQuiescer(fail_unquiesce=True))
    outcome = run_backup(req)
    assert outcome.status is BackupStatus.USABLE
    assert outcome.artifact_path is not None and outcome.artifact_path.exists()
    assert outcome.unquiesce_warning is not None
    assert outcome.unquiesce_warning.code == "UNQUIESCE_FAILED"
