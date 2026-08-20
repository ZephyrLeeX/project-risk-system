"""Versioned backup manifest (ADR 0031 §3).

The manifest is the tamper-evident binding of a backup set: it records every
component's name, size and sha256, plus the encryption metadata (KEK key
version only — never key material). It is encrypted as the first component of
the artifact and its canonical byte form is the associated data for every other
component, so component substitution or reordering is detected by the AEAD tag.

Canonical serialization is deterministic (sorted keys, compact separators,
UTF-8) so the encryptor and decryptor derive a bit-identical AAD.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class BackupType(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class BackupStatus(StrEnum):
    USABLE = "USABLE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class PgComponent:
    file: str
    pgDumpFormat: str  # "custom" (-Fc)
    sourcePgVersion: str
    alembicHead: str
    sizeBytes: int
    sha256: str
    schemaHash: str | None = None


@dataclass(frozen=True, slots=True)
class FilesComponent:
    file: str
    rootPath: str
    entryCount: int
    sizeBytes: int
    sha256: str
    excludes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EncryptionMeta:
    algorithm: str  # "AES-256-GCM"
    aead: str  # "AES-256-GCM"
    kekKeyVersion: str
    wrapEnvelopeFormat: str  # "rpenc:v1"
    payloadNonceRef: str  # nonce scheme descriptor, never the nonce/DEK


@dataclass(frozen=True, slots=True)
class BackupManifest:
    manifestFormatVersion: str
    backupId: str
    backupType: BackupType
    createdAt: str  # UTC RFC 3339 with milliseconds
    pg: PgComponent
    files: FilesComponent
    encryption: EncryptionMeta
    retentionClass: BackupType
    status: BackupStatus
    createdBy: str
    traceId: str

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict with deterministic key ordering."""

        data = asdict(self)
        # StrEnum values must serialize as their string value.
        data["backupType"] = self.backupType.value
        data["retentionClass"] = self.retentionClass.value
        data["status"] = self.status.value
        return data

    def to_canonical_bytes(self) -> bytes:
        """Deterministic UTF-8 encoding used as AEAD associated data."""

        return json.dumps(
            self.to_canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.to_canonical_bytes()).hexdigest()


def manifest_from_dict(data: dict[str, Any]) -> BackupManifest:
    """Parse a decrypted manifest dict, validating closed enum fields."""

    return BackupManifest(
        manifestFormatVersion=data["manifestFormatVersion"],
        backupId=data["backupId"],
        backupType=BackupType(data["backupType"]),
        createdAt=data["createdAt"],
        pg=PgComponent(**data["pg"]),
        files=FilesComponent(**data["files"]),
        encryption=EncryptionMeta(**data["encryption"]),
        retentionClass=BackupType(data["retentionClass"]),
        status=BackupStatus(data["status"]),
        createdBy=data["createdBy"],
        traceId=data["traceId"],
    )


def manifest_from_bytes(raw: bytes) -> BackupManifest:
    """Parse canonical manifest bytes (the decrypted manifest component)."""

    return manifest_from_dict(json.loads(raw.decode("utf-8")))


__all__ = [
    "BackupManifest",
    "BackupStatus",
    "BackupType",
    "EncryptionMeta",
    "FilesComponent",
    "PgComponent",
    "manifest_from_bytes",
    "manifest_from_dict",
]
