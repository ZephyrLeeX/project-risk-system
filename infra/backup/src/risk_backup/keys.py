"""Backup KEK loading and per-backup DEK wrapping (ADR 0031 §4, §5).

The backup KEK is a 256-bit key loaded from host read-only files, exactly the
``KeyRing.from_files`` model (one active encrypt version + retained decrypt
versions); it is independent from the application ``DATA_ENCRYPTION_KEY`` and is
never read from the process environment. Each backup draws a fresh random DEK;
the DEK is wrapped by the active KEK version using ``risk_platform.shared.crypto``
(``rpenc`` AES-256-GCM envelope, KEK version authenticated as AAD). The artifact
stores only the wrapped DEK envelope and the KEK key version.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path

from risk_backup.errors import BackupError
from risk_platform.shared.crypto import KeyRing, SecretCipher, SecretCryptoError

_DEK_BYTES = 32


@dataclass(frozen=True, slots=True)
class BackupKeyRing:
    """Active backup KEK version + retained decrypt versions."""

    key_ring: KeyRing
    active_version: str

    @property
    def cipher(self) -> SecretCipher:
        return SecretCipher(self.key_ring)


def load_backup_key_ring(active_version: str, kek_files: dict[str, Path]) -> BackupKeyRing:
    """Load base64-encoded 32-byte KEKs from explicit read-only host paths."""

    try:
        key_ring = KeyRing.from_files(active_version=active_version, key_files=kek_files)
    except SecretCryptoError as exc:
        raise BackupError("BACKUP_KEK_LOAD_FAILED", cause=exc) from exc
    return BackupKeyRing(key_ring=key_ring, active_version=active_version)


def new_dek() -> bytes:
    """Draw a fresh 256-bit data-encryption key for one backup."""

    return os.urandom(_DEK_BYTES)


def wrap_dek(ring: BackupKeyRing, dek: bytes) -> str:
    """Wrap a DEK with the active KEK version as an ``rpenc`` envelope string."""

    if len(dek) != _DEK_BYTES:
        raise BackupError("INVALID_DEK_LENGTH")
    plaintext = base64.b64encode(dek).decode("ascii")
    try:
        return ring.cipher.encrypt(plaintext).envelope
    except SecretCryptoError as exc:
        raise BackupError("DEK_WRAP_FAILED", cause=exc) from exc


def unwrap_dek(ring: BackupKeyRing, wrapped_dek: str, expected_kek_version: str) -> bytes:
    """Unwrap a DEK; the envelope's KEK version must match the artifact header.

    ``KeyRing`` retains historical decrypt versions, so old backups decrypt with
    the version they were created with — never re-encrypted (ADR 0031 §6).
    """

    try:
        plaintext = ring.cipher.decrypt(wrapped_dek)
        dek = base64.b64decode(plaintext, validate=True)
    except SecretCryptoError as exc:
        raise BackupError("DEK_UNWRAP_FAILED", cause=exc) from exc
    except (ValueError, binascii.Error) as exc:
        raise BackupError("DEK_UNWRAP_FAILED", cause=exc) from exc
    if len(dek) != _DEK_BYTES:
        raise BackupError("INVALID_DEK_LENGTH")
    # The rpenc envelope carries its own key version; assert it matches the
    # artifact outer header so a header/keystore mismatch fails closed.
    envelope_version = _envelope_key_version(wrapped_dek)
    if envelope_version != expected_kek_version:
        raise BackupError("KEK_VERSION_MISMATCH")
    return dek


def _envelope_key_version(envelope: str) -> str:
    # rpenc:v1:<key-version>:<nonce>:<ct> — see risk_platform.shared.crypto.
    parts = envelope.split(":")
    if len(parts) != 5:
        raise BackupError("DEK_UNWRAP_FAILED")
    return parts[2]


__all__ = [
    "BackupKeyRing",
    "load_backup_key_ring",
    "new_dek",
    "unwrap_dek",
    "wrap_dek",
]
