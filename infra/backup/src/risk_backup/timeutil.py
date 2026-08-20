"""Timestamp and backup-id helpers (ADR 0031 §3, §7)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def rfc3339_ms(dt: datetime) -> str:
    """UTC RFC 3339 with millisecond precision and trailing ``Z``."""

    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{dt.astimezone(UTC).microsecond // 1000:03d}Z"
    )


def compact_utc(dt: datetime) -> str:
    """Compact UTC stamp for ``backupId``: ``YYYYMMDDTHHMMSSZ``."""

    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def compute_backup_id(
    backup_type: str,
    dt: datetime,
    *,
    pg_sha256: str,
    files_sha256: str,
    alembic_head: str,
) -> str:
    """Stable, sortable backup id (ADR 0031 §7).

    ``<backupType>-<UTC YYYYMMDDTHHMMSSZ>-<8 hex>``. The 8-hex suffix hashes the
    component sha256s + alembic head (the manifest's binding content) rather than
    the full manifest, which would be circular on ``backupId``. Charset and
    length satisfy the ADR 0027 ``BACKUP_COPY`` resourceId constraint.
    """

    digest = hashlib.sha256(
        f"{backup_type}:{alembic_head}:{pg_sha256}:{files_sha256}".encode()
    ).hexdigest()[:8]
    return f"{backup_type}-{compact_utc(dt)}-{digest}"


__all__ = ["compact_utc", "compute_backup_id", "rfc3339_ms", "utc_now"]
