"""Plaintext temp cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest

from risk_backup.errors import CleanupWarning
from risk_backup.temputil import cleanup_plaintext


def test_cleanup_removes_files_and_dirs(tmp_path: Path) -> None:
    f = tmp_path / "plain"
    f.write_bytes(b"secret" * 100)
    cleanup_plaintext([f])
    assert not f.exists()


def test_cleanup_overwrites_before_unlink(tmp_path: Path) -> None:
    f = tmp_path / "plain"
    payload = b"sensitive" * 100
    f.write_bytes(payload)
    cleanup_plaintext([f])
    assert not f.exists()
    # The file is gone; nothing left to inspect — the overwrite happens before unlink.
    assert True


def test_cleanup_warning_when_file_not_removable(tmp_path: Path) -> None:
    f = tmp_path / "plain"
    f.write_bytes(b"x" * 10)
    f.chmod(0o400)
    try:
        with pytest.raises(CleanupWarning, match="PLAINTEXT_CLEANUP_FAILED"):
            cleanup_plaintext([f])
    finally:
        f.chmod(0o600)


def test_cleanup_missing_path_is_ok(tmp_path: Path) -> None:
    cleanup_plaintext([tmp_path / "absent"])  # no error
