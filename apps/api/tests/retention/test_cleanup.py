from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from risk_platform.retention.cleanup import (
    CleanupFailure,
    ImportSourceCleaner,
    OrphanTempCleaner,
)

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def test_import_source_cleaner_uses_canonical_uuid_target_and_retry_marker(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "imports").resolve()
    root.mkdir()
    batch_id = uuid4()
    batch_dir = root / str(batch_id)
    batch_dir.mkdir()
    source = batch_dir / "source.xlsx"
    source.write_bytes(b"workbook")
    cleaner = ImportSourceCleaner(root)

    marker = cleaner.prepare_delete(batch_id, f"{batch_id}/source.xlsx")

    assert marker is not None and marker.exists()
    assert not source.exists()
    assert cleaner.tombstone(batch_id) == f"retention-deleted:{batch_id}"
    # A retry after a DB rollback is safe because the marker proves this cleanup initiated deletion.
    assert cleaner.prepare_delete(batch_id, f"{batch_id}/source.xlsx") == marker
    cleaner.finish_delete(marker)
    assert not batch_dir.exists()


def test_import_source_cleaner_fails_closed_for_missing_or_unapproved_target(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "imports").resolve()
    root.mkdir()
    cleaner = ImportSourceCleaner(root)
    batch_id = uuid4()

    with pytest.raises(CleanupFailure, match="RETENTION_STORAGE_TARGET_UNSAFE"):
        cleaner.prepare_delete(batch_id, "../source.xlsx")
    with pytest.raises(CleanupFailure, match="RETENTION_SOURCE_MISSING"):
        cleaner.prepare_delete(batch_id, f"{batch_id}/source.xlsx")


def test_import_source_cleaner_rejects_batch_directory_symlink(tmp_path: Path) -> None:
    root = (tmp_path / "imports").resolve()
    root.mkdir()
    source_batch = uuid4()
    target_batch = uuid4()
    target_dir = root / str(target_batch)
    target_dir.mkdir()
    target_source = target_dir / "source.xlsx"
    target_source.write_bytes(b"other batch")
    (root / str(source_batch)).symlink_to(target_dir, target_is_directory=True)

    with pytest.raises(CleanupFailure, match="RETENTION_STORAGE_TARGET_UNSAFE"):
        ImportSourceCleaner(root).prepare_delete(
            source_batch, f"{source_batch}/source.xlsx"
        )
    assert target_source.read_bytes() == b"other batch"


def test_finish_delete_preserves_retry_marker_when_unexpected_content_remains(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "imports").resolve()
    root.mkdir()
    batch_id = uuid4()
    batch_dir = root / str(batch_id)
    batch_dir.mkdir()
    marker = batch_dir / ".retention-delete"
    marker.write_bytes(b"")
    residue = batch_dir / "source.xlsx"
    residue.write_bytes(b"restored after tombstone")
    cleaner = ImportSourceCleaner(root)

    with pytest.raises(CleanupFailure, match="RETENTION_STORAGE_RESIDUE_PRESENT"):
        cleaner.finish_delete(marker)
    assert marker.exists()
    assert residue.exists()


def test_finish_pending_removes_marker_missing_empty_directory(tmp_path: Path) -> None:
    root = (tmp_path / "imports").resolve()
    root.mkdir()
    batch_id = uuid4()
    batch_dir = root / str(batch_id)
    batch_dir.mkdir()

    ImportSourceCleaner(root).finish_pending(batch_id)

    assert not batch_dir.exists()


def test_finish_pending_rejects_marker_missing_residue_and_restores_marker(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "imports").resolve()
    root.mkdir()
    batch_id = uuid4()
    batch_dir = root / str(batch_id)
    batch_dir.mkdir()
    residue = batch_dir / "source.xlsx"
    residue.write_bytes(b"sensitive residue")

    with pytest.raises(CleanupFailure, match="RETENTION_STORAGE_RESIDUE_PRESENT"):
        ImportSourceCleaner(root).finish_pending(batch_id)
    assert residue.exists()
    assert (batch_dir / ".retention-delete").is_file()


def test_orphan_temp_cleanup_obeys_prefix_age_and_direct_child_boundary(tmp_path: Path) -> None:
    root = (tmp_path / "mail-temp").resolve()
    root.mkdir()
    stale = root / "risk-mail-stale"
    stale.mkdir()
    (stale / "input").write_text("temporary")
    recent = root / "risk-mail-recent"
    recent.mkdir()
    unrelated = root / "other-stale"
    unrelated.mkdir()
    old_timestamp = (NOW - timedelta(hours=2)).timestamp()
    os.utime(stale, (old_timestamp, old_timestamp))
    os.utime(unrelated, (old_timestamp, old_timestamp))
    os.utime(recent, (NOW.timestamp(), NOW.timestamp()))

    deleted, failed = OrphanTempCleaner(root).cleanup(as_of=NOW)

    assert (deleted, failed) == (1, 0)
    assert not stale.exists()
    assert recent.exists()
    assert unrelated.exists()


@pytest.mark.parametrize("root", [Path("relative"), Path("/")])
def test_cleanup_roots_must_be_explicit_and_narrow(root: Path) -> None:
    with pytest.raises(ValueError):
        ImportSourceCleaner(root)
    with pytest.raises(ValueError):
        OrphanTempCleaner(root)
