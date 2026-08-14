"""One-shot encrypted backup orchestrator (ADR 0031 §2, §4, §9).

Sequence: quiesce -> DB probes -> ``pg_dump -Fc`` -> file archive -> manifest ->
envelope-encrypt -> mandatory plaintext cleanup -> unquiesce. Any component
failure leaves no ``USABLE`` artifact (``INCOMPLETE``); plaintext temps are
cleaned on success and failure. Quiesce confirmation failure is fail-closed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from risk_backup import MANIFEST_FORMAT_VERSION
from risk_backup import db as dbmod
from risk_backup.archive import archive_files, file_sha256
from risk_backup.envelope import (
    CHUNK_MAX_DEFAULT,
    COMPONENT_FILES,
    COMPONENT_PGDUMP,
    write_artifact,
)
from risk_backup.errors import BackupError, CleanupWarning
from risk_backup.keys import BackupKeyRing, new_dek, wrap_dek
from risk_backup.manifest import (
    BackupManifest,
    BackupStatus,
    BackupType,
    EncryptionMeta,
    FilesComponent,
    PgComponent,
)
from risk_backup.pgdump import PgDumper
from risk_backup.quiesce import NoopQuiescer, Quiescer
from risk_backup.temputil import cleanup_plaintext
from risk_backup.timeutil import compute_backup_id, rfc3339_ms, utc_now


@dataclass(frozen=True, slots=True)
class BackupRequest:
    backup_type: BackupType
    dsn: str  # PostgreSQL DSN for metadata probes (alembic head / schema hash / version)
    pg_dumper: PgDumper
    storage_root: Path  # durable volume root (e.g. /app/storage)
    output_path: Path  # encrypted artifact output
    backup_key_ring: BackupKeyRing
    quiescer: Quiescer = field(default_factory=NoopQuiescer)
    temp_dir: Path = field(default_factory=lambda: Path("/tmp/risk-backup"))
    created_by: str = "operator"
    trace_id: str = "backup"
    chunk_max_bytes: int = CHUNK_MAX_DEFAULT
    excludes: list[str] | None = None


@dataclass(slots=True)
class BackupOutcome:
    status: BackupStatus
    manifest: BackupManifest | None = None
    artifact_path: Path | None = None
    cleanup_warning: CleanupWarning | None = None
    unquiesce_warning: BackupError | None = None
    error: BackupError | None = None

    @property
    def usable(self) -> bool:
        return self.status is BackupStatus.USABLE and self.artifact_path is not None


def _validate_paths(req: BackupRequest) -> None:
    output_parent = req.output_path.resolve().parent
    storage = req.storage_root.resolve()
    temp = req.temp_dir.resolve()
    if temp == storage or storage in temp.parents or temp in storage.parents:
        raise BackupError("TEMP_DIR_OVERLAPS_STORAGE")
    if temp == output_parent or output_parent in temp.parents or temp in output_parent.parents:
        raise BackupError("TEMP_DIR_OVERLAPS_OUTPUT")
    if output_parent == storage or storage in output_parent.parents:
        raise BackupError("OUTPUT_OVERLAPS_STORAGE")


def _cleanup_all(temp_dir: Path) -> CleanupWarning | None:
    if not temp_dir.exists():
        return None
    try:
        cleanup_plaintext([temp_dir])
    except CleanupWarning as warn:
        # Best-effort: shutil.rmtree as a fallback so the temp dir is gone even
        # if overwrite failed; the warning is still surfaced.
        shutil.rmtree(temp_dir, ignore_errors=True)
        return warn
    return None


def run_backup(req: BackupRequest) -> BackupOutcome:
    _validate_paths(req)
    req.temp_dir.mkdir(parents=True, exist_ok=True)
    req.output_path.parent.mkdir(parents=True, exist_ok=True)

    primary_error: BackupError | None = None
    manifest: BackupManifest | None = None
    artifact_written = False

    # Quiesce (fail-closed): no capture if write paths cannot be stopped.
    try:
        req.quiescer.quiesce()
    except BackupError as exc:
        _cleanup_all(req.temp_dir)
        return BackupOutcome(status=BackupStatus.INCOMPLETE, error=exc)

    try:
        # --- DB metadata probes (metadata-only) ---
        conn = dbmod.connect(req.dsn)
        try:
            head = dbmod.alembic_head(conn)
            sch_hash = dbmod.schema_hash(conn)
            pg_version = dbmod.server_version(conn)
        finally:
            conn.close()

        # --- PostgreSQL capture (pg_dump -Fc) ---
        pgdump_path = req.temp_dir / "pgdump"
        req.pg_dumper.dump(pgdump_path)
        pg_sha = file_sha256(pgdump_path)
        pg_size = pgdump_path.stat().st_size

        # --- File capture (durable volume tar) ---
        files_path = req.temp_dir / "files.tar"
        extra_exclude = [req.output_path.resolve().parent] if _output_under_storage(req) else []
        files_info = archive_files(
            req.storage_root,
            files_path,
            excludes=req.excludes,
            extra_exclude_dirs=extra_exclude,
        )

        # --- Manifest + envelope encryption ---
        now = utc_now()
        backup_id = compute_backup_id(
            req.backup_type.value,
            now,
            pg_sha256=pg_sha,
            files_sha256=files_info.sha256,
            alembic_head=head,
        )
        dek = new_dek()
        wrapped_dek = wrap_dek(req.backup_key_ring, dek)
        manifest = BackupManifest(
            manifestFormatVersion=MANIFEST_FORMAT_VERSION,
            backupId=backup_id,
            backupType=req.backup_type,
            createdAt=rfc3339_ms(now),
            pg=PgComponent(
                file=f"{backup_id}.pgdump",
                pgDumpFormat="custom",
                sourcePgVersion=pg_version,
                alembicHead=head,
                sizeBytes=pg_size,
                sha256=pg_sha,
                schemaHash=sch_hash,
            ),
            files=FilesComponent(
                file=f"{backup_id}.files.tar",
                rootPath=files_info.root_path,
                entryCount=files_info.entry_count,
                sizeBytes=files_info.size_bytes,
                sha256=files_info.sha256,
                excludes=files_info.excludes,
            ),
            encryption=EncryptionMeta(
                algorithm="AES-256-GCM",
                aead="AES-256-GCM",
                kekKeyVersion=req.backup_key_ring.active_version,
                wrapEnvelopeFormat="rpenc:v1",
                payloadNonceRef="per-component-random-base-plus-chunk-index",
            ),
            retentionClass=req.backup_type,
            status=BackupStatus.USABLE,
            createdBy=req.created_by,
            traceId=req.trace_id,
        )
        write_artifact(
            req.output_path,
            kek_key_version=req.backup_key_ring.active_version,
            wrapped_dek=wrapped_dek,
            manifest_bytes=manifest.to_canonical_bytes(),
            data_components=[(COMPONENT_PGDUMP, pgdump_path), (COMPONENT_FILES, files_path)],
            chunk_max_bytes=req.chunk_max_bytes,
            dek=dek,
        )
        artifact_written = True
        # Zero the DEK from memory (best-effort; bytes are immutable, but drop refs).
        del dek
    except BackupError as exc:
        primary_error = exc
    finally:
        cleanup_warning = _cleanup_all(req.temp_dir)
        if not artifact_written:
            req.output_path.unlink(missing_ok=True)
        # Unquiesce regardless of outcome; surface but do not mask the primary error.
        unquiesce_warning: BackupError | None = None
        try:
            req.quiescer.unquiesce()
        except BackupError as exc:
            unquiesce_warning = exc

    if primary_error is not None:
        return BackupOutcome(
            status=BackupStatus.INCOMPLETE,
            artifact_path=req.output_path if artifact_written else None,
            cleanup_warning=cleanup_warning,
            unquiesce_warning=unquiesce_warning,
            error=primary_error,
        )
    # Success: artifact is USABLE. Cleanup/unquiesce warnings are surfaced separately.
    return BackupOutcome(
        status=BackupStatus.USABLE,
        manifest=manifest,
        artifact_path=req.output_path,
        cleanup_warning=cleanup_warning,
        unquiesce_warning=unquiesce_warning,
    )


def _output_under_storage(req: BackupRequest) -> bool:
    storage = req.storage_root.resolve()
    output_parent = req.output_path.resolve().parent
    return output_parent == storage or storage in output_parent.parents


__all__ = ["BackupOutcome", "BackupRequest", "run_backup"]
