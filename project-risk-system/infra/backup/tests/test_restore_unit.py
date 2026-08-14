"""Restore fail-closed negatives that do not require a live database.

Each test builds a real encrypted artifact (via the envelope) then exercises the
restore orchestrator up to the point where it must refuse. The DB / pg_restore
path is covered by the real-PostgreSQL integration test.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from risk_backup import MANIFEST_FORMAT_VERSION
from risk_backup.envelope import COMPONENT_FILES, COMPONENT_PGDUMP, write_artifact
from risk_backup.keys import BackupKeyRing, new_dek, wrap_dek
from risk_backup.manifest import (
    BackupManifest,
    BackupStatus,
    BackupType,
    EncryptionMeta,
    FilesComponent,
    PgComponent,
)
from risk_backup.pgdump import PgConnection, PgDumper
from risk_backup.restore import RestoreRequest, run_restore

KeyRingFactory = Callable[[str, list[str]], BackupKeyRing]

_PG_DUMP = b"PGDUMP-PLAINTEXT-PAYLOAD"
_FILES_TAR = b"FILESARCHIVE-PLAINTEXT-PAYLOAD"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(
    *,
    pg_sha: str,
    files_sha: str,
    status: BackupStatus = BackupStatus.USABLE,
    alembic_head: str = "20260812_0008",
) -> BackupManifest:
    return BackupManifest(
        manifestFormatVersion=MANIFEST_FORMAT_VERSION,
        backupId="daily-20260815T023000Z-deadbeef",
        backupType=BackupType.DAILY,
        createdAt="2026-08-15T02:30:00.000Z",
        pg=PgComponent(
            file="daily-20260815T023000Z-deadbeef.pgdump",
            pgDumpFormat="custom",
            sourcePgVersion="16.4",
            alembicHead=alembic_head,
            sizeBytes=len(_PG_DUMP),
            sha256=pg_sha,
        ),
        files=FilesComponent(
            file="daily-20260815T023000Z-deadbeef.files.tar",
            rootPath="/app/storage",
            entryCount=1,
            sizeBytes=len(_FILES_TAR),
            sha256=files_sha,
        ),
        encryption=EncryptionMeta(
            algorithm="AES-256-GCM",
            aead="AES-256-GCM",
            kekKeyVersion="v1",
            wrapEnvelopeFormat="rpenc:v1",
            payloadNonceRef="per-component-random-base-plus-chunk-index",
        ),
        retentionClass=BackupType.DAILY,
        status=status,
        createdBy="tester",
        traceId="trace-1",
    )


def _build_artifact(
    tmp_path: Path,
    key_ring: BackupKeyRing,
    *,
    manifest_bytes: bytes,
    pg_dump: bytes = _PG_DUMP,
    files_tar: bytes = _FILES_TAR,
) -> Path:
    artifact = tmp_path / "artifact.rpbk"
    work = tmp_path / "build"
    work.mkdir()
    pg_path = work / "pgdump"
    pg_path.write_bytes(pg_dump)
    files_path = work / "files.tar"
    files_path.write_bytes(files_tar)
    dek = new_dek()
    wrapped = wrap_dek(key_ring, dek)
    write_artifact(
        artifact,
        kek_key_version=key_ring.active_version,
        wrapped_dek=wrapped,
        manifest_bytes=manifest_bytes,
        data_components=[(COMPONENT_PGDUMP, pg_path), (COMPONENT_FILES, files_path)],
        chunk_max_bytes=4096,
        dek=dek,
    )
    return artifact


def _restore_req(tmp_path: Path, artifact: Path, key_ring: BackupKeyRing) -> RestoreRequest:
    return RestoreRequest(
        artifact_path=artifact,
        target_dsn="postgresql://u:p@h/d",
        pg_dumper=PgDumper(connection=PgConnection(username="u", database="d", socket_dir="/tmp")),
        target_storage_root=tmp_path / "restore-storage",
        backup_key_ring=key_ring,
        plaintext_dir=tmp_path / "plain",
    )


def test_missing_kek_version_fails_closed(
    tmp_path: Path,
    default_key_ring: BackupKeyRing,
    key_ring_factory: KeyRingFactory,
) -> None:
    artifact = _build_artifact(
        tmp_path,
        default_key_ring,
        manifest_bytes=_manifest(pg_sha=_sha(_PG_DUMP), files_sha=_sha(_FILES_TAR))
        .to_canonical_bytes(),
    )
    # Restore with a key ring that does not retain the v1 KEK used to wrap the DEK.
    other_ring = key_ring_factory("v2", ["v2"])
    outcome = run_restore(_restore_req(tmp_path, artifact, other_ring))
    assert not outcome.ok
    assert outcome.error is not None and outcome.error.code == "MISSING_KEK_VERSION"


def test_tampered_payload_aead_auth_fails(
    tmp_path: Path, default_key_ring: BackupKeyRing
) -> None:
    artifact = _build_artifact(
        tmp_path,
        default_key_ring,
        manifest_bytes=_manifest(pg_sha=_sha(_PG_DUMP), files_sha=_sha(_FILES_TAR))
        .to_canonical_bytes(),
    )
    # Flip the last byte (inside the files component ciphertext -> GCM tag failure).
    blob = bytearray(artifact.read_bytes())
    blob[-1] ^= 0xFF
    artifact.write_bytes(blob)
    outcome = run_restore(_restore_req(tmp_path, artifact, default_key_ring))
    assert not outcome.ok
    assert outcome.error is not None and outcome.error.code == "ARTIFACT_AEAD_AUTH_FAILED"


def test_pgdump_sha256_mismatch_fails_closed(
    tmp_path: Path, default_key_ring: BackupKeyRing
) -> None:
    real_pg_sha = _sha(_PG_DUMP)
    # Manifest declares a wrong pgdump sha256.
    manifest = _manifest(pg_sha="0" * 64, files_sha=_sha(_FILES_TAR))
    artifact = _build_artifact(
        tmp_path, default_key_ring, manifest_bytes=manifest.to_canonical_bytes()
    )
    assert real_pg_sha != "0" * 64
    outcome = run_restore(_restore_req(tmp_path, artifact, default_key_ring))
    assert not outcome.ok
    assert outcome.error is not None and outcome.error.code == "PGDUMP_HASH_MISMATCH"


def test_corrupted_manifest_fails_closed(
    tmp_path: Path, default_key_ring: BackupKeyRing
) -> None:
    artifact = _build_artifact(
        tmp_path, default_key_ring, manifest_bytes=b"not-valid-json{"
    )
    outcome = run_restore(_restore_req(tmp_path, artifact, default_key_ring))
    assert not outcome.ok
    assert outcome.error is not None and outcome.error.code == "MANIFEST_INVALID"


def test_manifest_not_usable_fails_closed(
    tmp_path: Path, default_key_ring: BackupKeyRing
) -> None:
    manifest = _manifest(
        pg_sha=_sha(_PG_DUMP), files_sha=_sha(_FILES_TAR), status=BackupStatus.INCOMPLETE
    )
    artifact = _build_artifact(
        tmp_path, default_key_ring, manifest_bytes=manifest.to_canonical_bytes()
    )
    outcome = run_restore(_restore_req(tmp_path, artifact, default_key_ring))
    assert not outcome.ok
    assert outcome.error is not None and outcome.error.code == "MANIFEST_NOT_USABLE"
