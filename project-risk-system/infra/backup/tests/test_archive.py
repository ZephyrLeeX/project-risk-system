"""Durable file archiver: deterministic tar, excludes, path-traversal guard."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from risk_backup.archive import archive_files, extract_files, file_sha256
from risk_backup.errors import BackupError


def _seed_storage(root: Path) -> None:
    excel = root / "excel" / "batch-1"
    excel.mkdir(parents=True)
    (excel / "source.xlsx").write_bytes(b"XLSX-CONTENT")
    (root / "mail").mkdir(parents=True)
    (root / "mail" / ".tmp_partial").write_bytes(b"partial")  # excluded by default


def test_archive_then_extract_round_trips(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    _seed_storage(storage)
    tar_path = tmp_path / "files.tar"
    info = archive_files(storage, tar_path)
    assert info.entry_count == 1  # only the xlsx; .tmp_partial excluded
    dest = tmp_path / "restore"
    extract_files(tar_path, dest)
    assert (dest / "excel" / "batch-1" / "source.xlsx").read_bytes() == b"XLSX-CONTENT"
    assert not (dest / "mail" / ".tmp_partial").exists()


def test_archive_is_deterministic(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    _seed_storage(storage)
    tar1 = tmp_path / "a.tar"
    tar2 = tmp_path / "b.tar"
    info1 = archive_files(storage, tar1)
    info2 = archive_files(storage, tar2)
    assert info1.sha256 == info2.sha256
    assert tar1.read_bytes() == tar2.read_bytes()


def test_excludes_applied(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    (storage / "excel").mkdir(parents=True)
    (storage / "excel" / "keep.xlsx").write_bytes(b"keep")
    (storage / "excel" / "drop.tmp").write_bytes(b"drop")
    info = archive_files(storage, tmp_path / "f.tar")
    assert info.entry_count == 1
    dest = tmp_path / "r"
    extract_files(tmp_path / "f.tar", dest)
    assert (dest / "excel" / "keep.xlsx").exists()
    assert not (dest / "excel" / "drop.tmp").exists()


def test_missing_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="FILES_ROOT_MISSING"):
        archive_files(tmp_path / "nope", tmp_path / "f.tar")


def test_path_traversal_in_archive_fails_closed(tmp_path: Path) -> None:
    # Hand-craft a tar with a member escaping the restore root.
    tar_path = tmp_path / "evil.tar"
    info = tarfile.TarInfo(name="../escaped.xlsx")
    info.size = 1
    info.mtime = 0
    with tarfile.open(tar_path, "w") as tar:
        tar.addfile(info, io.BytesIO(b"X"))
    dest = tmp_path / "restore"
    with pytest.raises(BackupError, match=r"FILES_ARCHIVE_PATH_ESCAPE|FILES_ARCHIVE_CORRUPT"):
        extract_files(tar_path, dest)


def test_file_sha256_stable(tmp_path: Path) -> None:
    p = tmp_path / "x"
    p.write_bytes(b"abc")
    assert file_sha256(p) == file_sha256(p)
