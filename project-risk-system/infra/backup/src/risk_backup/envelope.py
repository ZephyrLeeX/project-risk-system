"""AES-256-GCM chunked artifact container (ADR 0031 §4).

The encrypted backup artifact is a self-describing binary container:

* a small plaintext outer header — format magic/version, KEK key version, the
  ``rpenc``-wrapped DEK, the AEAD algorithm, the chunk size and the ordered
  component names. The header never contains key material, plaintext or hashes;
  it exposes only what key lookup needs (ADR 0031 §4);
* then each component (``manifest`` first, then ``pgdump`` and ``files``) as a
  per-component random base nonce plus a length-prefixed stream of GCM ciphertext
  chunks.

Three integrity layers (ADR 0031 §4): the DEK-wrap GCM tag (verified on unwrap),
every payload chunk's GCM tag, and the manifest component sha256s (verified after
decrypt). The manifest is encrypted as the first component; its canonical bytes
are the associated data for every other component, so component substitution or
reordering is detected. Each chunk's AAD also binds the component name and chunk
index, defeating chunk reordering within a component.

Data components are streamed to/from plaintext temp files so the full plaintext
never sits in memory; only the small manifest is held in memory.
"""

from __future__ import annotations

import json
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from risk_backup import BACKUP_FORMAT_VERSION
from risk_backup.errors import BackupError

MAGIC = b"RPBK1"
CHUNK_MAX_DEFAULT = 4 * 1024 * 1024
NONCE_BYTES = 12
TAG_BYTES = 16

COMPONENT_MANIFEST = "manifest"
COMPONENT_PGDUMP = "pgdump"
COMPONENT_FILES = "files"

_U32 = struct.Struct(">I")

# AAD separator byte that cannot appear in the ASCII component name / index.
_AAD_SEP = b"\x00"


@dataclass(frozen=True, slots=True)
class ArtifactHeader:
    format_version: int
    kek_key_version: str
    wrapped_dek: str
    aead: str
    chunk_max_bytes: int
    components: list[str]

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            {
                "format": "rpbk",
                "formatVersion": self.format_version,
                "kekKeyVersion": self.kek_key_version,
                "wrappedDek": self.wrapped_dek,
                "aead": self.aead,
                "chunkMaxBytes": self.chunk_max_bytes,
                "components": list(self.components),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class DecryptedArtifact:
    header: ArtifactHeader
    manifest_bytes: bytes
    data_components: dict[str, Path]  # name -> plaintext temp file


def _derive_nonce(base_nonce: bytes, index: int) -> bytes:
    """Derive a unique 12-byte nonce per chunk from a per-component base.

    The low 4 bytes are XOR-ed with the chunk index; distinct indices yield
    distinct nonces from the same base, so (DEK, nonce) is never reused within a
    component. Base nonces are random per component, so cross-component reuse is
    negligible (2**96 space).
    """

    if index < 0 or index >= 2**32:
        raise BackupError("CHUNK_INDEX_OUT_OF_RANGE")
    counter = int.from_bytes(base_nonce[8:], "big") ^ index
    return base_nonce[:8] + counter.to_bytes(4, "big")


def _chunk_aad(
    component_name: str,
    index: int,
    manifest_bytes: bytes,
    kek_key_version: str,
) -> bytes:
    """Associated data for one chunk.

    The manifest component is bound to the artifact's KEK version (its own bytes
    are not yet available to bind against). Every other component is bound to the
    full canonical manifest bytes plus the component name and chunk index.
    """

    if component_name == COMPONENT_MANIFEST:
        return (
            f"rpbk:{BACKUP_FORMAT_VERSION}:{COMPONENT_MANIFEST}:{kek_key_version}:{index}"
        ).encode("ascii")
    return manifest_bytes + _AAD_SEP + f"{component_name}:{index}".encode("ascii")


def _iter_chunks(plaintext: bytes, chunk_max: int) -> Iterator[bytes]:
    if not plaintext:
        yield b""
        return
    for i in range(0, len(plaintext), chunk_max):
        yield plaintext[i : i + chunk_max]


def write_artifact(
    out_path: Path,
    *,
    kek_key_version: str,
    wrapped_dek: str,
    manifest_bytes: bytes,
    data_components: list[tuple[str, Path]],
    chunk_max_bytes: int = CHUNK_MAX_DEFAULT,
    dek: bytes,
) -> None:
    """Write the encrypted artifact.

    ``data_components`` is an ordered list of (name, plaintext-file-path) pairs
    streamed from disk. The manifest component is written first from
    ``manifest_bytes``.
    """

    if len(dek) != 32:
        raise BackupError("INVALID_DEK_LENGTH")
    component_names = [COMPONENT_MANIFEST, *[name for name, _ in data_components]]
    header = ArtifactHeader(
        format_version=BACKUP_FORMAT_VERSION,
        kek_key_version=kek_key_version,
        wrapped_dek=wrapped_dek,
        aead="AES-256-GCM",
        chunk_max_bytes=chunk_max_bytes,
        components=component_names,
    )
    header_bytes = header.to_json_bytes()
    aesgcm = AESGCM(dek)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        with tmp_path.open("wb") as out:
            out.write(MAGIC)
            out.write(_U32.pack(len(header_bytes)))
            out.write(header_bytes)
            # Manifest component (small, in memory).
            _write_component(
                out,
                aesgcm=aesgcm,
                component_name=COMPONENT_MANIFEST,
                plaintext=manifest_bytes,
                manifest_bytes=manifest_bytes,
                kek_key_version=kek_key_version,
                chunk_max=chunk_max_bytes,
            )
            for name, plain_path in data_components:
                _write_component_stream(
                    out,
                    aesgcm=aesgcm,
                    component_name=name,
                    plain_path=plain_path,
                    manifest_bytes=manifest_bytes,
                    kek_key_version=kek_key_version,
                    chunk_max=chunk_max_bytes,
                )
        os.replace(tmp_path, out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_component(
    out: BinaryIO,
    *,
    aesgcm: AESGCM,
    component_name: str,
    plaintext: bytes,
    manifest_bytes: bytes,
    kek_key_version: str,
    chunk_max: int,
) -> None:
    base_nonce = os.urandom(NONCE_BYTES)
    chunks = list(_iter_chunks(plaintext, chunk_max))
    out.write(base_nonce)
    out.write(_U32.pack(len(chunks)))
    for i, chunk in enumerate(chunks):
        aad = _chunk_aad(component_name, i, manifest_bytes, kek_key_version)
        nonce = _derive_nonce(base_nonce, i)
        ct = aesgcm.encrypt(nonce, chunk, aad)
        out.write(_U32.pack(len(ct)))
        out.write(ct)


def _write_component_stream(
    out: BinaryIO,
    *,
    aesgcm: AESGCM,
    component_name: str,
    plain_path: Path,
    manifest_bytes: bytes,
    kek_key_version: str,
    chunk_max: int,
) -> None:
    base_nonce = os.urandom(NONCE_BYTES)
    chunks_written = 0
    # Two-pass over the file: first count chunks (cheap seek), then encrypt.
    file_size = plain_path.stat().st_size
    chunk_count = max(1, (file_size + chunk_max - 1) // chunk_max)
    out.write(base_nonce)
    out.write(_U32.pack(chunk_count))
    index = 0
    with plain_path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_max)
            if not chunk:
                break
            aad = _chunk_aad(component_name, index, manifest_bytes, kek_key_version)
            nonce = _derive_nonce(base_nonce, index)
            ct = aesgcm.encrypt(nonce, chunk, aad)
            out.write(_U32.pack(len(ct)))
            out.write(ct)
            index += 1
            chunks_written += 1
    if chunks_written == 0:
        # Empty file: emit one empty-plaintext chunk.
        aad = _chunk_aad(component_name, 0, manifest_bytes, kek_key_version)
        nonce = _derive_nonce(base_nonce, 0)
        ct = aesgcm.encrypt(nonce, b"", aad)
        out.write(_U32.pack(len(ct)))
        out.write(ct)
        chunks_written = 1
    if chunks_written != chunk_count:
        raise BackupError("ARTIFACT_CHUNK_COUNT_MISMATCH")


def read_artifact_header(path: Path) -> ArtifactHeader:
    """Read only the plaintext outer header (for KEK lookup before decrypt)."""

    with path.open("rb") as handle:
        magic = handle.read(len(MAGIC))
        if magic != MAGIC:
            raise BackupError("ARTIFACT_MAGIC_INVALID")
        (header_len,) = _U32.unpack(handle.read(_U32.size))
        header_bytes = handle.read(header_len)
        if len(header_bytes) != header_len:
            raise BackupError("ARTIFACT_HEADER_TRUNCATED")
    data = json.loads(header_bytes.decode("utf-8"))
    if data.get("formatVersion") != BACKUP_FORMAT_VERSION:
        raise BackupError("ARTIFACT_FORMAT_VERSION_UNSUPPORTED")
    return ArtifactHeader(
        format_version=data["formatVersion"],
        kek_key_version=data["kekKeyVersion"],
        wrapped_dek=data["wrappedDek"],
        aead=data["aead"],
        chunk_max_bytes=data["chunkMaxBytes"],
        components=list(data["components"]),
    )


def decrypt_artifact(
    path: Path,
    dek: bytes,
    *,
    plaintext_dir: Path,
) -> DecryptedArtifact:
    """Decrypt the artifact, streaming data components to ``plaintext_dir``.

    The manifest component is decrypted in memory first so its canonical bytes
    can authenticate every other component. Any AEAD tag failure, framing error
    or truncation raises ``BackupError`` (fail-closed; never partial).
    """

    if len(dek) != 32:
        raise BackupError("INVALID_DEK_LENGTH")
    header = read_artifact_header(path)
    if header.aead != "AES-256-GCM":
        raise BackupError("ARTIFACT_AEAD_UNSUPPORTED")
    aesgcm = AESGCM(dek)
    plaintext_dir.mkdir(parents=True, exist_ok=True)
    with path.open("rb") as handle:
        handle.read(len(MAGIC))
        handle.read(_U32.size)
        handle.read(len(header.to_json_bytes()))
        components = header.components
        if not components or components[0] != COMPONENT_MANIFEST:
            raise BackupError("ARTIFACT_COMPONENT_ORDER_INVALID")
        manifest_bytes = _read_component(
            handle,
            aesgcm=aesgcm,
            component_name=COMPONENT_MANIFEST,
            manifest_bytes=b"",
            kek_key_version=header.kek_key_version,
        )
        data_paths: dict[str, Path] = {}
        for name in components[1:]:
            target = plaintext_dir / name
            _read_component_stream(
                handle,
                aesgcm=aesgcm,
                component_name=name,
                manifest_bytes=manifest_bytes,
                kek_key_version=header.kek_key_version,
                out_path=target,
            )
            data_paths[name] = target
    return DecryptedArtifact(
        header=header, manifest_bytes=manifest_bytes, data_components=data_paths
    )


def _read_component(
    handle: BinaryIO,
    *,
    aesgcm: AESGCM,
    component_name: str,
    manifest_bytes: bytes,
    kek_key_version: str,
) -> bytes:
    base_nonce = handle.read(NONCE_BYTES)
    if len(base_nonce) != NONCE_BYTES:
        raise BackupError("ARTIFACT_TRUNCATED")
    (chunk_count,) = _U32.unpack(_exact_read(handle, _U32.size))
    parts: list[bytes] = []
    for i in range(chunk_count):
        (ct_len,) = _U32.unpack(_exact_read(handle, _U32.size))
        ct = _exact_read(handle, ct_len)
        aad = _chunk_aad(component_name, i, manifest_bytes, kek_key_version)
        nonce = _derive_nonce(base_nonce, i)
        try:
            parts.append(aesgcm.decrypt(nonce, ct, aad))
        except InvalidTag as exc:
            raise BackupError("ARTIFACT_AEAD_AUTH_FAILED", cause=exc) from exc
    return b"".join(parts)


def _read_component_stream(
    handle: BinaryIO,
    *,
    aesgcm: AESGCM,
    component_name: str,
    manifest_bytes: bytes,
    kek_key_version: str,
    out_path: Path,
) -> None:
    base_nonce = handle.read(NONCE_BYTES)
    if len(base_nonce) != NONCE_BYTES:
        raise BackupError("ARTIFACT_TRUNCATED")
    (chunk_count,) = _U32.unpack(_exact_read(handle, _U32.size))
    with out_path.open("wb") as out:
        for i in range(chunk_count):
            (ct_len,) = _U32.unpack(_exact_read(handle, _U32.size))
            ct = _exact_read(handle, ct_len)
            aad = _chunk_aad(component_name, i, manifest_bytes, kek_key_version)
            nonce = _derive_nonce(base_nonce, i)
            try:
                out.write(aesgcm.decrypt(nonce, ct, aad))
            except InvalidTag as exc:
                raise BackupError("ARTIFACT_AEAD_AUTH_FAILED", cause=exc) from exc


def _exact_read(handle: BinaryIO, length: int) -> bytes:
    data = handle.read(length)
    if len(data) != length:
        raise BackupError("ARTIFACT_TRUNCATED")
    return data


__all__ = [
    "CHUNK_MAX_DEFAULT",
    "COMPONENT_FILES",
    "COMPONENT_MANIFEST",
    "COMPONENT_PGDUMP",
    "MAGIC",
    "ArtifactHeader",
    "DecryptedArtifact",
    "decrypt_artifact",
    "read_artifact_header",
    "write_artifact",
]
