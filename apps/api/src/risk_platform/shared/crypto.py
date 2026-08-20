"""Versioned authenticated encryption for persisted application credentials.

Envelope format (ASCII)::

    rpenc:v1:<key-version>:<base64url nonce>:<base64url ciphertext-and-tag>

The key version and format prefix are authenticated as AES-GCM associated data.  The
key material itself is loaded from caller-selected read-only files (including Docker
Secret mounts); this module deliberately does not read process environment variables.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENVELOPE_PREFIX = "rpenc"
_FORMAT_VERSION = "v1"
_KEY_VERSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")
_NONCE_BYTES = 12
_KEY_BYTES = 32


class SecretCryptoError(RuntimeError):
    """A stable error which never includes key material, ciphertext, or plaintext."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SecretEnvelope:
    """Parsed, self-describing encrypted credential."""

    key_version: str
    nonce: bytes
    ciphertext: bytes

    def serialize(self) -> str:
        return ":".join(
            (
                _ENVELOPE_PREFIX,
                _FORMAT_VERSION,
                self.key_version,
                _b64encode(self.nonce),
                _b64encode(self.ciphertext),
            )
        )

    @classmethod
    def parse(cls, value: str) -> SecretEnvelope:
        try:
            prefix, format_version, key_version, nonce, ciphertext = value.split(":")
            if prefix != _ENVELOPE_PREFIX or format_version != _FORMAT_VERSION:
                raise ValueError
            _validate_key_version(key_version)
            decoded_nonce = _b64decode(nonce)
            decoded_ciphertext = _b64decode(ciphertext)
            if len(decoded_nonce) != _NONCE_BYTES or len(decoded_ciphertext) < 16:
                raise ValueError
        except (ValueError, binascii.Error):
            raise SecretCryptoError("INVALID_SECRET_ENVELOPE") from None
        return cls(
            key_version=key_version,
            nonce=decoded_nonce,
            ciphertext=decoded_ciphertext,
        )

    @property
    def associated_data(self) -> bytes:
        return f"{_ENVELOPE_PREFIX}:{_FORMAT_VERSION}:{self.key_version}".encode("ascii")


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """Persistence-safe encrypted value and separately display-safe mask."""

    envelope: str
    masked: str


@dataclass(frozen=True, slots=True)
class LegacySecretFields:
    """AES-GCM ciphertext/IV/tag values stored in legacy database columns."""

    ciphertext: str
    iv: str
    auth_tag: str
    key_version: str


@dataclass(frozen=True, slots=True)
class KeyRing:
    """Immutable key ring with one active encryption key and retained decrypt keys."""

    active_version: str
    keys: Mapping[str, bytes]

    def __post_init__(self) -> None:
        _validate_key_version(self.active_version)
        copied: dict[str, bytes] = {}
        for version, key in self.keys.items():
            _validate_key_version(version)
            if len(key) != _KEY_BYTES:
                raise SecretCryptoError("INVALID_ENCRYPTION_KEY")
            copied[version] = bytes(key)
        if self.active_version not in copied:
            raise SecretCryptoError("ACTIVE_KEY_NOT_FOUND")
        object.__setattr__(self, "keys", MappingProxyType(copied))

    @classmethod
    def from_files(cls, active_version: str, key_files: Mapping[str, Path]) -> KeyRing:
        """Load base64-encoded 32-byte keys from explicit read-only secret paths."""

        loaded: dict[str, bytes] = {}
        for version, path in key_files.items():
            _validate_key_version(version)
            try:
                encoded = path.read_text(encoding="ascii").strip()
                loaded[version] = base64.b64decode(encoded, validate=True)
            except (OSError, UnicodeError, ValueError, binascii.Error):
                raise SecretCryptoError("ENCRYPTION_KEY_LOAD_FAILED") from None
        return cls(active_version=active_version, keys=loaded)

    def key_for(self, version: str) -> bytes:
        try:
            return self.keys[version]
        except KeyError:
            raise SecretCryptoError("ENCRYPTION_KEY_NOT_FOUND") from None


class SecretCipher:
    """Encrypt, decrypt, and rotate credentials against a versioned key ring."""

    def __init__(self, key_ring: KeyRing) -> None:
        self._key_ring = key_ring

    def encrypt(self, plaintext: str) -> EncryptedSecret:
        version = self._key_ring.active_version
        nonce = os.urandom(_NONCE_BYTES)
        template = SecretEnvelope(version, nonce, b"")
        ciphertext = AESGCM(self._key_ring.key_for(version)).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            template.associated_data,
        )
        envelope = SecretEnvelope(version, nonce, ciphertext).serialize()
        return EncryptedSecret(envelope=envelope, masked=mask_secret(plaintext))

    def decrypt(self, serialized: str) -> str:
        envelope = SecretEnvelope.parse(serialized)
        key = self._key_ring.key_for(envelope.key_version)
        try:
            plaintext = AESGCM(key).decrypt(
                envelope.nonce,
                envelope.ciphertext,
                envelope.associated_data,
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError):
            raise SecretCryptoError("SECRET_DECRYPTION_FAILED") from None

    def needs_rotation(self, serialized: str) -> bool:
        return SecretEnvelope.parse(serialized).key_version != self._key_ring.active_version

    def rotate(self, serialized: str) -> EncryptedSecret:
        """Re-encrypt using the active key; old keys remain decrypt-only."""

        return self.encrypt(self.decrypt(serialized))

    def encrypt_legacy(self, plaintext: str) -> LegacySecretFields:
        """Encrypt directly for the legacy ciphertext/IV/tag database columns."""

        version = self._key_ring.active_version
        nonce = os.urandom(_NONCE_BYTES)
        combined = AESGCM(self._key_ring.key_for(version)).encrypt(
            nonce, plaintext.encode("utf-8"), None
        )
        return LegacySecretFields(
            ciphertext=base64.b64encode(combined[:-16]).decode("ascii"),
            iv=base64.b64encode(nonce).decode("ascii"),
            auth_tag=base64.b64encode(combined[-16:]).decode("ascii"),
            key_version=version,
        )

    def decrypt_legacy(self, fields: LegacySecretFields) -> str:
        """Decrypt the current NestJS AES-GCM triplet with an explicit key version."""

        try:
            _validate_key_version(fields.key_version)
            nonce = base64.b64decode(fields.iv, validate=True)
            ciphertext = base64.b64decode(fields.ciphertext, validate=True)
            tag = base64.b64decode(fields.auth_tag, validate=True)
            if len(nonce) != _NONCE_BYTES or len(tag) != 16:
                raise ValueError
            plaintext = AESGCM(self._key_ring.key_for(fields.key_version)).decrypt(
                nonce,
                ciphertext + tag,
                None,
            )
            return plaintext.decode("utf-8")
        except SecretCryptoError:
            raise
        except (InvalidTag, UnicodeDecodeError, ValueError, binascii.Error):
            raise SecretCryptoError("SECRET_DECRYPTION_FAILED") from None

    def rotate_legacy(self, fields: LegacySecretFields) -> EncryptedSecret:
        """Convert a legacy triplet into the active self-describing envelope."""

        return self.encrypt(self.decrypt_legacy(fields))


def mask_secret(value: str, visible_suffix: int = 4) -> str:
    """Return a display-safe mask without exposing a short secret in full."""

    if visible_suffix < 0:
        raise ValueError("visible_suffix must not be negative")
    if not value or visible_suffix == 0 or len(value) <= visible_suffix:
        return "•" * max(8, len(value))
    return f"{'•' * 12}{value[-visible_suffix:]}"


def _validate_key_version(version: str) -> None:
    if _KEY_VERSION_PATTERN.fullmatch(version) is None:
        raise SecretCryptoError("INVALID_KEY_VERSION")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


__all__ = [
    "EncryptedSecret",
    "KeyRing",
    "LegacySecretFields",
    "SecretCipher",
    "SecretCryptoError",
    "SecretEnvelope",
    "mask_secret",
]
