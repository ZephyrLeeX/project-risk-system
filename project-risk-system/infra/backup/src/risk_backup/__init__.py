"""Encrypted backup and restore for the project-risk-system (ADR 0031).

One-shot backup/restore commands that produce a quiesce-coordinated, consistent
backup set (PostgreSQL ``pg_dump -Fc`` + durable file storage), protected by
AES-256-GCM envelope encryption that reuses ``risk_platform.shared.crypto``
(``rpenc`` / ``KeyRing``) for DEK wrapping, and a tamper-evident manifest. Restore
is fail-closed into an isolated target with three-layer integrity verification.

This package owns only ``infra/backup/**``; it reads — never edits — the frozen
application composition, Compose topology and crypto module.
"""

from __future__ import annotations

__all__ = [
    "BACKUP_FORMAT_VERSION",
    "MANIFEST_FORMAT_VERSION",
    "BackupError",
]

MANIFEST_FORMAT_VERSION = "v1"
BACKUP_FORMAT_VERSION = 1
