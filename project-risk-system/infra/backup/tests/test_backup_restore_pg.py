"""Real PostgreSQL 16 backup/restore drill + fail-closed negatives (ADR 0031 §8).

Provisions dedicated databases (created + migrated per test), seeds a realistic
backup set (user -> durable task -> import batch + audit hash-chain entry + a
durable storage file), runs the real ``pg_dump -Fc`` / ``pg_restore`` path via
the postgres container, and asserts the end-to-end round-trip plus the fail-closed
negatives that genuinely require a live database.

Skipped when ``TEST_DATABASE_URL`` is unset or the postgres container / pg_dump
is not reachable via docker.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from risk_backup import MANIFEST_FORMAT_VERSION
from risk_backup.backup import BackupRequest, run_backup
from risk_backup.envelope import (
    COMPONENT_FILES,
    COMPONENT_PGDUMP,
    decrypt_artifact,
    read_artifact_header,
    write_artifact,
)
from risk_backup.keys import BackupKeyRing, new_dek, unwrap_dek, wrap_dek
from risk_backup.manifest import (
    BackupManifest,
    BackupStatus,
    BackupType,
    EncryptionMeta,
    FilesComponent,
    PgComponent,
)
from risk_backup.pgdump import PgConnection, PgDumper
from risk_backup.quiesce import NoopQuiescer
from risk_backup.restore import RestoreOutcome, RestoreRequest, run_restore

REPO = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO / "apps" / "api-python" / "alembic.ini"
POSTGRES_CONTAINER = "project-risk-postgres"
PG_USER = "project_risk"
PG_SOCKET = "/var/run/postgresql"

KeyRingFactory = Callable[[str, list[str]], BackupKeyRing]


def _docker_pg_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "exec", POSTGRES_CONTAINER, "pg_dump", "--version"],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL") or not _docker_pg_available(),
    reason="TEST_DATABASE_URL or docker postgres/pg_dump unavailable; real drill skipped",
)


def _dsn_for(db_name: str) -> str:
    base = os.environ["TEST_DATABASE_URL"]
    return re.sub(r"/[^/]*$", f"/{db_name}", base)


def _create_db(db_name: str) -> None:
    with psycopg.connect(_dsn_for("risk_test")) as admin:
        admin.autocommit = True
        admin.execute(f'CREATE DATABASE "{db_name}"')


def _drop_db(db_name: str) -> None:
    with psycopg.connect(_dsn_for("risk_test")) as admin:
        admin.autocommit = True
        admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


def _migrate_db(db_name: str) -> None:
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", _dsn_for(db_name))
    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            config = Config(str(ALEMBIC_INI))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
    finally:
        engine.dispose()


def _dumper(db_name: str) -> PgDumper:
    return PgDumper(
        connection=PgConnection(username=PG_USER, database=db_name, socket_dir=PG_SOCKET),
        runner=["docker", "exec", "-i", POSTGRES_CONTAINER],
    )


def _seed(
    dsn: str,
    storage_root: Path,
    *,
    file_present: bool = True,
    break_audit: bool = False,
) -> str:
    """Seed a realistic backup set; return the import storageKey."""
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    storage_key = f"{batch_id}/source.xlsx"
    now = datetime.now(UTC)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "users" ("id","username","passwordHash","displayName","updatedAt") '
                "VALUES (%s,%s,%s,%s,%s)",
                (user_id, f"u{user_id.hex[:12]}", "x", "Drill User", now),
            )
            cur.execute(
                'INSERT INTO "durable_tasks" '
                '("id","kind","idempotencyKey","payload","maxAttempts","updatedAt") '
                "VALUES (%s,%s::\"DurableTaskKind\",%s,%s::jsonb,%s,%s)",
                (task_id, "IMPORT_PREVIEW", f"key-{task_id}", "{}", 3, now),
            )
            cur.execute(
                'INSERT INTO "import_batches" ("id","taskId","fileName","fileHash","storageKey",'
                '"sheetName","totalRows","readyRows","warningRows","errorRows","uploadedById",'
                '"sourceExpiresAt","retentionConfigVersion") '
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    batch_id,
                    task_id,
                    "source.xlsx",
                    "0" * 64,
                    storage_key,
                    "Sheet1",
                    1,
                    1,
                    0,
                    0,
                    user_id,
                    datetime(2030, 1, 1, tzinfo=UTC),
                    "v1",
                ),
            )
            cur.execute(
                'INSERT INTO "audit_logs" '
                '("id","actorUserId","actorType","module","action",'
                '"resourceType","result","traceId") '
                'VALUES (%s,%s,%s::"AuditActorType",%s,%s,%s,%s::"AuditResult",%s)',
                (
                    uuid.uuid4(),
                    user_id,
                    "USER",
                    "BACKUP",
                    "BACKUP_DRILL_SEED",
                    "BACKUP_COPY",
                    "SUCCESS",
                    str(uuid.uuid4()),
                ),
            )
            if break_audit:
                cur.execute(
                    'INSERT INTO "audit_logs" '
                    '("id","actorUserId","actorType","module","action",'
                    '"resourceType","result","traceId") '
                    'VALUES (%s,%s,%s::"AuditActorType",%s,%s,%s,%s::"AuditResult",%s)',
                    (
                        uuid.uuid4(),
                        user_id,
                        "USER",
                        "BACKUP",
                        "BACKUP_DRILL_VERIFY",
                        "BACKUP_COPY",
                        "SUCCESS",
                        str(uuid.uuid4()),
                    ),
                )
                # Corrupt the second row's integrityHash, bypassing the append-only guard.
                cur.execute('ALTER TABLE "audit_logs" DISABLE TRIGGER "audit_logs_reject_update"')
                cur.execute(
                    'UPDATE "audit_logs" SET "integrityHash" = %s WHERE "action" = %s',
                    ("0" * 64, "BACKUP_DRILL_VERIFY"),
                )
                cur.execute('ALTER TABLE "audit_logs" ENABLE TRIGGER "audit_logs_reject_update"')
        conn.commit()
    if file_present:
        fpath = storage_root / "excel" / storage_key
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_bytes(b"XLSX-DRILL-CONTENT")
    return storage_key


def _count(dsn: str, table: str) -> int:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{table}"')
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


class _Drill:
    def __init__(
        self, tmp_path: Path, key_ring_factory: KeyRingFactory, create: Callable[[str], None]
    ) -> None:
        self._create = create
        self.src_db = "rbk_src_" + uuid.uuid4().hex[:12]
        create(self.src_db)
        _migrate_db(self.src_db)
        self.storage_root = tmp_path / "storage"
        self.storage_root.mkdir(parents=True)
        self.key_ring = key_ring_factory("v1", ["v1"])

    @property
    def dsn(self) -> str:
        return _dsn_for(self.src_db)

    def dumper(self, db_name: str | None = None) -> PgDumper:
        return _dumper(db_name or self.src_db)

    def new_target(self) -> tuple[str, str]:
        name = "rbk_tgt_" + uuid.uuid4().hex[:12]
        self._create(name)  # empty, not migrated
        return name, _dsn_for(name)

    def backup(self, tmp_path: Path, *, output_name: str = "backup.rpbk") -> Path:
        out = tmp_path / "out" / output_name
        req = BackupRequest(
            backup_type=BackupType.DAILY,
            dsn=self.dsn,
            pg_dumper=self.dumper(),
            storage_root=self.storage_root,
            output_path=out,
            backup_key_ring=self.key_ring,
            quiescer=NoopQuiescer(),
            temp_dir=tmp_path / "tmp",
            created_by="drill",
            trace_id="drill",
            chunk_max_bytes=4096,
        )
        outcome = run_backup(req)
        assert outcome.usable, f"backup not usable: {outcome.error}"
        return out

    def restore(
        self, tmp_path: Path, artifact: Path, key_ring: BackupKeyRing | None = None
    ) -> tuple[RestoreOutcome, str]:
        target_db, target_dsn = self.new_target()
        req = RestoreRequest(
            artifact_path=artifact,
            target_dsn=target_dsn,
            pg_dumper=self.dumper(target_db),
            target_storage_root=tmp_path / "restore",
            backup_key_ring=key_ring or self.key_ring,
            plaintext_dir=tmp_path / "plain",
        )
        return run_restore(req), target_dsn


@pytest.fixture
def drill(tmp_path: Path, key_ring_factory: KeyRingFactory) -> Iterator[_Drill]:
    created: list[str] = []

    def create(name: str) -> None:
        _create_db(name)
        created.append(name)

    d = _Drill(tmp_path, key_ring_factory, create)
    yield d
    for name in reversed(created):
        _drop_db(name)


def test_successful_backup_then_isolated_restore_round_trip(
    drill: _Drill, tmp_path: Path
) -> None:
    storage_key = _seed(drill.dsn, drill.storage_root)
    artifact = drill.backup(tmp_path)
    outcome, target_dsn = drill.restore(tmp_path, artifact)

    assert outcome.ok, f"restore failed: {outcome.error}"
    assert outcome.manifest is not None
    # Audit hash-chain verified on the restored DB (ADR 0008).
    assert outcome.audit_total_records == outcome.audit_verified_records
    assert outcome.audit_verified_records is not None
    assert outcome.audit_verified_records >= 1
    # Reconcile: referenced file present, no orphans, no missing.
    assert outcome.reconcile is not None
    assert outcome.reconcile.missing == []

    # The restored target DB carries the same row counts as the source.
    assert _count(target_dsn, "users") == 1
    assert _count(target_dsn, "durable_tasks") == 1
    assert _count(target_dsn, "import_batches") == 1
    assert _count(drill.dsn, "users") == _count(target_dsn, "users")
    # The restored storage file exists with the original content.
    restored_file = tmp_path / "restore" / "excel" / storage_key
    assert restored_file.read_bytes() == b"XLSX-DRILL-CONTENT"
    assert outcome.manifest.files.entryCount >= 1


def test_pg_and_files_consistency_after_restore(drill: _Drill, tmp_path: Path) -> None:
    storage_key = _seed(drill.dsn, drill.storage_root)
    artifact = drill.backup(tmp_path)
    outcome, target_dsn = drill.restore(tmp_path, artifact)
    assert outcome.ok
    # The restored DB references exactly the file that was restored.
    assert _fetch_keys(target_dsn) == [storage_key]
    assert (tmp_path / "restore" / "excel" / storage_key).exists()


def test_missing_referenced_file_fails_closed(drill: _Drill, tmp_path: Path) -> None:
    # DB references a storageKey whose file is absent from the durable volume.
    _seed(drill.dsn, drill.storage_root, file_present=False)
    artifact = drill.backup(tmp_path)
    outcome, _ = drill.restore(tmp_path, artifact)
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.code == "RESTORE_MISSING_REFERENCED_FILE"


def test_orphan_file_is_discarded_during_restore(drill: _Drill, tmp_path: Path) -> None:
    storage_key = _seed(drill.dsn, drill.storage_root)
    # An orphan file the DB does not reference.
    orphan = drill.storage_root / "excel" / "extra" / "orphan.xlsx"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"ORPHAN")
    artifact = drill.backup(tmp_path)
    outcome, _ = drill.restore(tmp_path, artifact)
    assert outcome.ok
    assert outcome.reconcile is not None
    assert any("orphan.xlsx" in o for o in outcome.reconcile.orphans_removed)
    assert outcome.reconcile.missing == []
    assert not (tmp_path / "restore" / "excel" / "extra" / "orphan.xlsx").exists()
    assert (tmp_path / "restore" / "excel" / storage_key).exists()


def test_active_and_historical_kek_restore(
    drill: _Drill, tmp_path: Path, key_ring_factory: KeyRingFactory
) -> None:
    _seed(drill.dsn, drill.storage_root)
    artifact = drill.backup(tmp_path)
    # Later, the active KEK rotates to v2 but v1 is retained for historical decrypt.
    rotated = key_ring_factory("v2", ["v1", "v2"])
    outcome, _ = drill.restore(tmp_path, artifact, key_ring=rotated)
    assert outcome.ok, f"historical-KEK restore failed: {outcome.error}"


def test_broken_audit_hash_chain_fails_closed(drill: _Drill, tmp_path: Path) -> None:
    _seed(drill.dsn, drill.storage_root, break_audit=True)
    artifact = drill.backup(tmp_path)
    outcome, _ = drill.restore(tmp_path, artifact)
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.code == "AUDIT_CHAIN_BROKEN"


def test_corrupted_db_archive_fails_closed(drill: _Drill, tmp_path: Path) -> None:
    # A real-looking artifact whose pgdump component is garbage (sha256 matches the
    # garbage, so layers 1-3 pass) but pg_restore cannot load it.
    garbage_pg = b"NOT-A-PG-DUMP" * 16
    garbage_files = b"NOT-A-TAR"
    artifact = _build_custom_artifact(
        tmp_path / "custom",
        drill.key_ring,
        pg_dump=garbage_pg,
        files_tar=garbage_files,
    )
    outcome, _ = drill.restore(tmp_path, artifact)
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.code == "PG_RESTORE_FAILED"


def test_corrupted_file_archive_fails_closed(drill: _Drill, tmp_path: Path) -> None:
    _seed(drill.dsn, drill.storage_root)
    real_artifact = drill.backup(tmp_path)
    # Recover the real pgdump plaintext so pg_restore succeeds, then pair it with a
    # corrupt (non-tar) file archive so extraction fails after the DB is restored.
    real_pgdump = _extract_component(real_artifact, drill.key_ring, COMPONENT_PGDUMP)
    corrupt_files = b"CORRUPT-NOT-A-TAR-PAYLOAD"
    artifact = _build_custom_artifact(
        tmp_path / "custom",
        drill.key_ring,
        pg_dump=real_pgdump,
        files_tar=corrupt_files,
    )
    outcome, _ = drill.restore(tmp_path, artifact)
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.code == "FILES_ARCHIVE_CORRUPT"


# --- helpers for the custom-artifact negatives ---


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_for(
    *, pg_dump: bytes, files_tar: bytes, key_ring: BackupKeyRing
) -> BackupManifest:
    return BackupManifest(
        manifestFormatVersion=MANIFEST_FORMAT_VERSION,
        backupId="daily-20260815T030000Z-feedface",
        backupType=BackupType.DAILY,
        createdAt="2026-08-15T03:00:00.000Z",
        pg=PgComponent(
            file="daily-20260815T030000Z-feedface.pgdump",
            pgDumpFormat="custom",
            sourcePgVersion="16.4",
            alembicHead="20260812_0008",
            sizeBytes=len(pg_dump),
            sha256=_sha(pg_dump),
        ),
        files=FilesComponent(
            file="daily-20260815T030000Z-feedface.files.tar",
            rootPath="/app/storage",
            entryCount=1,
            sizeBytes=len(files_tar),
            sha256=_sha(files_tar),
        ),
        encryption=EncryptionMeta(
            algorithm="AES-256-GCM",
            aead="AES-256-GCM",
            kekKeyVersion=key_ring.active_version,
            wrapEnvelopeFormat="rpenc:v1",
            payloadNonceRef="per-component-random-base-plus-chunk-index",
        ),
        retentionClass=BackupType.DAILY,
        status=BackupStatus.USABLE,
        createdBy="drill",
        traceId="drill",
    )


def _build_custom_artifact(
    work_dir: Path,
    key_ring: BackupKeyRing,
    *,
    pg_dump: bytes,
    files_tar: bytes,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    pg_path = work_dir / "pgdump"
    pg_path.write_bytes(pg_dump)
    files_path = work_dir / "files.tar"
    files_path.write_bytes(files_tar)
    artifact = work_dir / "artifact.rpbk"
    dek = new_dek()
    wrapped = wrap_dek(key_ring, dek)
    write_artifact(
        artifact,
        kek_key_version=key_ring.active_version,
        wrapped_dek=wrapped,
        manifest_bytes=_manifest_for(pg_dump=pg_dump, files_tar=files_tar, key_ring=key_ring)
        .to_canonical_bytes(),
        data_components=[(COMPONENT_PGDUMP, pg_path), (COMPONENT_FILES, files_path)],
        chunk_max_bytes=4096,
        dek=dek,
    )
    return artifact


def _extract_component(artifact: Path, key_ring: BackupKeyRing, name: str) -> bytes:
    import tempfile

    with tempfile.TemporaryDirectory() as plain_dir:
        header = read_artifact_header(artifact)
        dek = unwrap_dek(key_ring, header.wrapped_dek, header.kek_key_version)
        decrypted = decrypt_artifact(artifact, dek, plaintext_dir=Path(plain_dir))
        return decrypted.data_components[name].read_bytes()


def _fetch_keys(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('SELECT "storageKey" FROM "import_batches" ORDER BY "storageKey"')
        return [str(r[0]) for r in cur.fetchall()]
