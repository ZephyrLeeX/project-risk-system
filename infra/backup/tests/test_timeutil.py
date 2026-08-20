"""backupId / timestamp helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from risk_backup.timeutil import compact_utc, compute_backup_id, rfc3339_ms, utc_now


def test_rfc3339_ms_has_milliseconds_and_z() -> None:
    dt = datetime(2026, 8, 15, 2, 30, 0, 123456, tzinfo=UTC)
    assert rfc3339_ms(dt) == "2026-08-15T02:30:00.123Z"


def test_compact_utc_format() -> None:
    dt = datetime(2026, 8, 15, 2, 30, 0, tzinfo=UTC)
    assert compact_utc(dt) == "20260815T023000Z"


def test_backup_id_format_and_charset() -> None:
    dt = utc_now()
    bid = compute_backup_id(
        "daily", dt, pg_sha256="a" * 64, files_sha256="b" * 64, alembic_head="20260812_0008"
    )
    assert bid.startswith("daily-")
    assert re.fullmatch(r"daily-\d{8}T\d{6}Z-[0-9a-f]{8}", bid)
    assert 1 <= len(bid) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", bid)


def test_backup_id_differs_for_different_content() -> None:
    dt = utc_now()
    a = compute_backup_id("daily", dt, pg_sha256="a" * 64, files_sha256="b" * 64, alembic_head="h1")
    b = compute_backup_id("daily", dt, pg_sha256="c" * 64, files_sha256="b" * 64, alembic_head="h1")
    assert a != b
