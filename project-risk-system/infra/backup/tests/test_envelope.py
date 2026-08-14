"""Envelope container: AES-256-GCM chunked AEAD round-trip and tamper detection."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from risk_backup import BACKUP_FORMAT_VERSION
from risk_backup.envelope import (
    _U32,
    CHUNK_MAX_DEFAULT,
    COMPONENT_FILES,
    COMPONENT_MANIFEST,
    COMPONENT_PGDUMP,
    MAGIC,
    DecryptedArtifact,
    decrypt_artifact,
    read_artifact_header,
    write_artifact,
)
from risk_backup.errors import BackupError
from risk_backup.manifest import BackupManifest, manifest_from_bytes


def _make_manifest_bytes(seed: str = "m") -> bytes:
    # Minimal canonical-ish JSON; only determinism matters for these tests.
    return b'{"manifestFormatVersion":"v1","backupId":"daily-x-' + seed.encode() + b'"}'


def _write_artifact(
    tmp_path: Path,
    dek: bytes,
    *,
    manifest_bytes: bytes | None = None,
    pgdump: bytes = b"PGDUMP",
    files: bytes = b"FILES",
    chunk_max: int = CHUNK_MAX_DEFAULT,
    kek_version: str = "v1",
    wrapped_dek: str = "rpenc:v1:v1:AAAA:BBBB",
) -> tuple[Path, bytes]:
    manifest_bytes = manifest_bytes or _make_manifest_bytes()
    pg_path = tmp_path / "pg"
    files_path = tmp_path / "files"
    pg_path.write_bytes(pgdump)
    files_path.write_bytes(files)
    out = tmp_path / "artifact.rpbk"
    write_artifact(
        out,
        kek_key_version=kek_version,
        wrapped_dek=wrapped_dek,
        manifest_bytes=manifest_bytes,
        data_components=[(COMPONENT_PGDUMP, pg_path), (COMPONENT_FILES, files_path)],
        chunk_max_bytes=chunk_max,
        dek=dek,
    )
    return out, manifest_bytes


def test_round_trip_decrypts_manifest_and_components(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, manifest_bytes = _write_artifact(tmp_path, dek, pgdump=b"hello-pg", files=b"hello-files")
    plain_dir = tmp_path / "plain"
    result = decrypt_artifact(out, dek, plaintext_dir=plain_dir)
    assert result.manifest_bytes == manifest_bytes
    assert result.data_components[COMPONENT_PGDUMP].read_bytes() == b"hello-pg"
    assert result.data_components[COMPONENT_FILES].read_bytes() == b"hello-files"


def test_manifest_canonical_bytes_round_trip_for_aad(tmp_path: Path) -> None:
    """Reconstructed manifest bytes must equal the encrypted original (AAD binding)."""

    from risk_backup.manifest import (
        BackupStatus,
        BackupType,
        EncryptionMeta,
        FilesComponent,
        PgComponent,
    )

    manifest = BackupManifest(
        manifestFormatVersion="v1",
        backupId="daily-20260815T020000Z-ab12cd34",
        backupType=BackupType.DAILY,
        createdAt="2026-08-15T02:00:00.000Z",
        pg=PgComponent(
            file="x.pgdump",
            pgDumpFormat="custom",
            sourcePgVersion="16.4",
            alembicHead="20260812_0008",
            sizeBytes=10,
            sha256="a" * 64,
            schemaHash="b" * 64,
        ),
        files=FilesComponent(
            file="x.files.tar",
            rootPath="/app/storage",
            entryCount=2,
            sizeBytes=20,
            sha256="c" * 64,
            excludes=["*.tmp"],
        ),
        encryption=EncryptionMeta(
            algorithm="AES-256-GCM",
            aead="AES-256-GCM",
            kekKeyVersion="v1",
            wrapEnvelopeFormat="rpenc:v1",
            payloadNonceRef="per-component-random-base-plus-chunk-index",
        ),
        retentionClass=BackupType.DAILY,
        status=BackupStatus.USABLE,
        createdBy="op",
        traceId="t",
    )
    original = manifest.to_canonical_bytes()
    rebuilt = manifest_from_bytes(original).to_canonical_bytes()
    assert original == rebuilt

    dek = b"k" * 32
    out, _ = _write_artifact(tmp_path, dek, manifest_bytes=original)
    result: DecryptedArtifact = decrypt_artifact(out, dek, plaintext_dir=tmp_path / "plain")
    # The decryptor must be able to rebuild identical AAD from the parsed manifest.
    assert manifest_from_bytes(result.manifest_bytes).to_canonical_bytes() == original


def test_wrong_dek_fails_closed(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, _ = _write_artifact(tmp_path, dek)
    with pytest.raises(BackupError, match="ARTIFACT_AEAD_AUTH_FAILED"):
        decrypt_artifact(out, b"other" + b"x" * 27, plaintext_dir=tmp_path / "plain")


def test_tampered_data_chunk_fails_closed(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, _ = _write_artifact(tmp_path, dek, pgdump=b"A" * 64)
    data = bytearray(out.read_bytes())
    data[-1] ^= 0xFF  # flip a byte in the last files chunk
    out.write_bytes(data)
    with pytest.raises(BackupError, match=r"ARTIFACT_AEAD_AUTH_FAILED|ARTIFACT_TRUNCATED"):
        decrypt_artifact(out, dek, plaintext_dir=tmp_path / "plain")


def test_tampered_manifest_fails_closed(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, _ = _write_artifact(tmp_path, dek)
    data = bytearray(out.read_bytes())
    # The manifest component follows the header: base_nonce(12) + chunk_count(4)
    # + per-chunk [ct_len(4) + ciphertext]. Flip a byte inside the ciphertext.
    header_len = struct.unpack(">I", bytes(data[5:9]))[0]
    manifest_ct_start = 9 + header_len + 12 + 4 + 4
    data[manifest_ct_start] ^= 0xFF
    out.write_bytes(data)
    with pytest.raises(BackupError, match="ARTIFACT_AEAD_AUTH_FAILED"):
        decrypt_artifact(out, dek, plaintext_dir=tmp_path / "plain")


def test_wrong_magic_fails_closed(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, _ = _write_artifact(tmp_path, dek)
    data = bytearray(out.read_bytes())
    data[0] = ord("X")
    out.write_bytes(data)
    with pytest.raises(BackupError, match="ARTIFACT_MAGIC_INVALID"):
        read_artifact_header(out)


def test_truncated_fails_closed(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, _ = _write_artifact(tmp_path, dek)
    data = out.read_bytes()[:-50]
    out.write_bytes(data)
    with pytest.raises(BackupError, match="ARTIFACT_TRUNCATED"):
        decrypt_artifact(out, dek, plaintext_dir=tmp_path / "plain")


def test_chunked_large_component_round_trips(tmp_path: Path) -> None:
    dek = b"k" * 32
    big = bytes(i % 251 for i in range(100_000))  # spans multiple small chunks
    out, _ = _write_artifact(tmp_path, dek, pgdump=big, chunk_max=4096)
    result = decrypt_artifact(out, dek, plaintext_dir=tmp_path / "plain")
    assert result.data_components[COMPONENT_PGDUMP].read_bytes() == big


def test_empty_component_round_trips(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, _ = _write_artifact(tmp_path, dek, pgdump=b"", files=b"")
    result = decrypt_artifact(out, dek, plaintext_dir=tmp_path / "plain")
    assert result.data_components[COMPONENT_PGDUMP].read_bytes() == b""
    assert result.data_components[COMPONENT_FILES].read_bytes() == b""


def test_header_exposes_only_metadata(tmp_path: Path) -> None:
    dek = b"k" * 32
    out, manifest_bytes = _write_artifact(tmp_path, dek)
    header = read_artifact_header(out)
    assert header.format_version == BACKUP_FORMAT_VERSION
    assert header.kek_key_version == "v1"
    assert header.aead == "AES-256-GCM"
    assert header.components == [COMPONENT_MANIFEST, COMPONENT_PGDUMP, COMPONENT_FILES]
    # The plaintext header must not contain the manifest plaintext or DEK.
    header_blob = out.read_bytes()[: 5 + _U32.size + len(header.to_json_bytes())]
    assert manifest_bytes not in header_blob
    assert dek not in header_blob
    assert MAGIC in header_blob[:5]
