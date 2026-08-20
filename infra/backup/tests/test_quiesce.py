"""Quiesce orchestration (Noop + Compose subprocess wiring)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from risk_backup.errors import BackupError
from risk_backup.quiesce import ComposeQuiescer, NoopQuiescer


class _FakeCompleted:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


def test_noop_quiescer_round_trips() -> None:
    q = NoopQuiescer()
    q.quiesce()
    q.unquiesce()  # no error


def test_compose_quiesce_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str], **kwargs: object) -> _FakeCompleted:
        calls.append(list(argv))
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    q = ComposeQuiescer(compose_file="infra/docker-compose.yml", project_dir=".")
    q.quiesce()
    q.unquiesce()
    assert calls[0][-4:] == ["stop", "api", "worker", "scheduler"]
    assert calls[1][-6:] == ["up", "-d", "--no-deps", "api", "worker", "scheduler"]


def test_compose_quiesce_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _FakeCompleted(1))
    q = ComposeQuiescer(compose_file="infra/docker-compose.yml", project_dir=".")
    with pytest.raises(BackupError, match="QUIESCE_FAILED"):
        q.quiesce()


def test_compose_unquiesce_failure_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _FakeCompleted(1))
    q = ComposeQuiescer(compose_file="infra/docker-compose.yml", project_dir=".")
    with pytest.raises(BackupError, match="UNQUIESCE_FAILED"):
        q.unquiesce()
