"""Restore file reconciliation: orphan discard + missing fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from risk_backup.errors import BackupError
from risk_backup.reconcile import reconcile_files


def _write_referenced(root: Path, key: str, content: bytes = b"x") -> None:
    target = root / "excel" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_referenced_files_present_succeeds(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    _write_referenced(root, "batch-1/source.xlsx")
    result = reconcile_files(root, ["batch-1/source.xlsx"])
    assert result.referenced_count == 1
    assert result.orphans_removed == []
    assert result.missing == []


def test_missing_referenced_file_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    _write_referenced(root, "batch-1/source.xlsx")
    with pytest.raises(BackupError, match="RESTORE_MISSING_REFERENCED_FILE"):
        reconcile_files(root, ["batch-1/source.xlsx", "batch-2/source.xlsx"])


def test_orphan_files_are_discarded(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    _write_referenced(root, "batch-1/source.xlsx")
    # An orphan not referenced by the DB.
    orphan = root / "excel" / "batch-1" / "orphan.xlsx"
    orphan.write_bytes(b"orphan")
    result = reconcile_files(root, ["batch-1/source.xlsx"])
    assert "excel/batch-1/orphan.xlsx" in result.orphans_removed
    assert not orphan.exists()
    assert (root / "excel" / "batch-1" / "source.xlsx").exists()


def test_unreferenced_area_orphans_discarded(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    _write_referenced(root, "batch-1/source.xlsx")
    (root / "mail").mkdir(parents=True)
    (root / "mail" / "stray.bin").write_bytes(b"stray")
    result = reconcile_files(root, ["batch-1/source.xlsx"])
    assert any("mail" in o for o in result.orphans_removed)
    assert not (root / "mail" / "stray.bin").exists()


def test_empty_references_discards_everything(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    _write_referenced(root, "batch-1/source.xlsx")
    result = reconcile_files(root, [])
    assert result.referenced_count == 0
    assert len(result.orphans_removed) == 1
    assert not (root / "excel" / "batch-1" / "source.xlsx").exists()
