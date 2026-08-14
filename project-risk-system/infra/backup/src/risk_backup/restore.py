"""One-shot fail-closed restore orchestrator (ADR 0031 §8).

Restore into an isolated empty target only. Sequence: KEK lookup -> DEK unwrap
(AEAD) -> payload decrypt (AEAD) -> manifest parse/validate -> component sha256
verify -> ``pg_restore`` into empty DB -> audit hash-chain verify -> alembic-head
match -> file extract -> orphan/missing reconcile. Any failure aborts with no
partial success claimed. Three integrity layers: DEK-wrap tag, payload chunk
tags, manifest component sha256s.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from risk_backup import MANIFEST_FORMAT_VERSION
from risk_backup import db as dbmod
from risk_backup.archive import extract_files, file_sha256
from risk_backup.envelope import (
    COMPONENT_FILES,
    COMPONENT_PGDUMP,
    decrypt_artifact,
    read_artifact_header,
)
from risk_backup.errors import BackupError, CleanupWarning
from risk_backup.keys import BackupKeyRing, unwrap_dek
from risk_backup.manifest import BackupManifest, BackupStatus, manifest_from_bytes
from risk_backup.pgdump import PgDumper
from risk_backup.reconcile import ReconcileResult, reconcile_files
from risk_backup.temputil import cleanup_plaintext


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    artifact_path: Path
    target_dsn: str  # isolated target DB DSN (for psycopg probes)
    pg_dumper: PgDumper  # pg_restore into the isolated target DB
    target_storage_root: Path  # isolated restore storage dir (must be empty/absent)
    backup_key_ring: BackupKeyRing  # must retain the artifact's KEK version
    plaintext_dir: Path  # temp for decrypted components
    import_subdir: str = "excel"


@dataclass(slots=True)
class RestoreOutcome:
    manifest: BackupManifest | None = None
    audit_total_records: int | None = None
    audit_verified_records: int | None = None
    reconcile: ReconcileResult | None = None
    cleanup_warning: CleanupWarning | None = None
    error: BackupError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.manifest is not None


def _cleanup_all(plaintext_dir: Path) -> CleanupWarning | None:
    if not plaintext_dir.exists():
        return None
    try:
        cleanup_plaintext([plaintext_dir])
    except CleanupWarning as warn:
        shutil.rmtree(plaintext_dir, ignore_errors=True)
        return warn
    return None


def run_restore(req: RestoreRequest) -> RestoreOutcome:
    outcome = RestoreOutcome()
    try:
        _run_restore(req, outcome)
    except BackupError as exc:
        outcome.error = exc
    finally:
        outcome.cleanup_warning = _cleanup_all(req.plaintext_dir)
    return outcome


def _run_restore(req: RestoreRequest, outcome: RestoreOutcome) -> None:
    # 1. Key lookup (fail-closed if the KEK version is not retained).
    header = read_artifact_header(req.artifact_path)
    if header.kek_key_version not in req.backup_key_ring.key_ring.keys:
        raise BackupError("MISSING_KEK_VERSION")

    # 2. DEK unwrap (rpenc AEAD tag verification; version must match header).
    dek = unwrap_dek(req.backup_key_ring, header.wrapped_dek, header.kek_key_version)

    # 3. Payload decrypt (per-chunk AEAD; manifest first to derive AAD).
    decrypted = decrypt_artifact(
        req.artifact_path, dek, plaintext_dir=req.plaintext_dir
    )
    del dek

    # 4. Manifest parse + validation.
    try:
        manifest = manifest_from_bytes(decrypted.manifest_bytes)
    except (ValueError, KeyError, TypeError) as exc:
        raise BackupError("MANIFEST_INVALID", cause=exc) from exc
    if manifest.manifestFormatVersion != MANIFEST_FORMAT_VERSION:
        raise BackupError("MANIFEST_FORMAT_VERSION_UNSUPPORTED")
    if manifest.status is not BackupStatus.USABLE:
        raise BackupError("MANIFEST_NOT_USABLE")
    if (
        COMPONENT_PGDUMP not in decrypted.data_components
        or COMPONENT_FILES not in decrypted.data_components
    ):
        raise BackupError("MANIFEST_COMPONENTS_INCOMPLETE")
    outcome.manifest = manifest

    # 5. Component sha256 verification (layer 3).
    pgdump_path = decrypted.data_components[COMPONENT_PGDUMP]
    files_path = decrypted.data_components[COMPONENT_FILES]
    if file_sha256(pgdump_path) != manifest.pg.sha256:
        raise BackupError("PGDUMP_HASH_MISMATCH")
    if file_sha256(files_path) != manifest.files.sha256:
        raise BackupError("FILES_HASH_MISMATCH")

    # 6. Isolated target must be empty (DB + storage).
    conn = dbmod.connect(req.target_dsn)
    try:
        dbmod.assert_database_empty(conn)
    finally:
        conn.close()
    _assert_storage_empty(req.target_storage_root)

    # 7. PostgreSQL restore into the isolated empty DB.
    req.pg_dumper.restore(pgdump_path, target_database=req.pg_dumper.connection.database)

    # 8. Audit hash-chain verification on the restored DB (ADR 0008).
    conn = dbmod.connect(req.target_dsn)
    try:
        audit = dbmod.audit_chain_status(conn)
        head = dbmod.alembic_head(conn)
    finally:
        conn.close()
    outcome.audit_total_records = audit.total_records
    outcome.audit_verified_records = audit.verified_records
    if not audit.valid:
        raise BackupError("AUDIT_CHAIN_BROKEN")
    if head != manifest.pg.alembicHead:
        raise BackupError("ALEMBIC_HEAD_MISMATCH")

    # 9. File restore + reconcile (orphan discard + missing fail-closed).
    extract_files(files_path, req.target_storage_root)
    conn = dbmod.connect(req.target_dsn)
    try:
        keys = dbmod.import_storage_keys(conn)
    finally:
        conn.close()
    outcome.reconcile = reconcile_files(
        req.target_storage_root, keys, import_subdir=req.import_subdir
    )


def _assert_storage_empty(root: Path) -> None:
    if root.exists():
        for _ in root.iterdir():
            raise BackupError("RESTORE_TARGET_NOT_EMPTY")
    else:
        root.mkdir(parents=True, exist_ok=True)


__all__ = ["RestoreOutcome", "RestoreRequest", "run_restore"]
