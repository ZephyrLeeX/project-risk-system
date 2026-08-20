"""Shared fixtures for the risk_backup test suite."""

from __future__ import annotations

import base64
import secrets
import uuid
from pathlib import Path
from typing import Protocol

import pytest

from risk_backup.keys import BackupKeyRing, load_backup_key_ring


def _write_kek(path: Path) -> None:
    key_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    path.write_text(key_b64 + "\n", encoding="ascii")
    path.chmod(0o400)


class KeyRingFactory(Protocol):
    def __call__(self, active: str, versions: list[str]) -> BackupKeyRing: ...


@pytest.fixture
def kek_dir(tmp_path: Path) -> Path:
    return tmp_path / "keys"


@pytest.fixture
def key_ring_factory(kek_dir: Path) -> KeyRingFactory:
    """Build a BackupKeyRing from generated base64 32-byte KEK files."""

    def _factory(active: str, versions: list[str]) -> BackupKeyRing:
        kek_dir.mkdir(parents=True, exist_ok=True)
        files: dict[str, Path] = {}
        for version in versions:
            path = kek_dir / f"kek_{version}"
            # Reuse an existing KEK file so a version keeps the same key across
            # factory calls (rotation tests need v1 to be byte-identical before
            # and after the active version moves to v2).
            if not path.exists():
                _write_kek(path)
            files[version] = path
        return load_backup_key_ring(active, files)

    return _factory


@pytest.fixture
def default_key_ring(key_ring_factory: KeyRingFactory) -> BackupKeyRing:
    return key_ring_factory("v1", ["v1"])


@pytest.fixture
def random_uuid() -> str:
    return str(uuid.uuid4())
