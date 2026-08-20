"""Quiesce orchestration for consistent backup capture (ADR 0031 §2).

Before capturing the PostgreSQL snapshot and file archive, the backup job
quiesces every write path so the DB snapshot and file state align (no new
durable writes, no cleanup/rollback, no mailbox sync in the window). The default
``ComposeQuiescer`` stops the API, worker and scheduler services (the three
write paths), leaving PostgreSQL and Redis up for capture; the API is stopped
rather than placed in a maintenance mode because adding such a mode is outside
T036's frozen write-set (ADR 0031 §11/§12), and a full stop is a strictly
stronger quiesce that still guarantees the consistency invariant. The drill uses
``NoopQuiescer`` (the isolated target has no concurrent writers).

Quiesce confirmation failure is fail-closed (no usable backup). Unquiesce
failure does not invalidate an already-complete encrypted artifact but is
surfaced so operators can restart the stack manually.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from risk_backup.errors import BackupError

# The three write paths. PostgreSQL + Redis stay up for capture; proxy/web are
# read-only presentation and need not stop.
DEFAULT_QUIESCE_SERVICES: tuple[str, ...] = ("api", "worker", "scheduler")


class Quiescer(Protocol):
    def quiesce(self) -> None: ...
    def unquiesce(self) -> None: ...


@dataclass(frozen=True, slots=True)
class NoopQuiescer:
    """No-op quiescer for isolated drills with no concurrent writers."""

    def quiesce(self) -> None:
        return None

    def unquiesce(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ComposeQuiescer:
    """Stop/restart write-path services via ``docker compose``.

    Production quiescer. Runs on the host (or a container with the docker
    socket) where the compose project is deployed.
    """

    compose_file: str
    project_dir: str
    services: list[str] = field(default_factory=lambda: list(DEFAULT_QUIESCE_SERVICES))
    env: dict[str, str] = field(default_factory=dict)

    def _base_argv(self) -> list[str]:
        return ["docker", "compose", "-f", self.compose_file]

    def quiesce(self) -> None:
        argv = [*self._base_argv(), "stop", *self.services]
        result = subprocess.run(
            argv,
            cwd=self.project_dir,
            capture_output=True,
            env=self.env or None,
            check=False,
        )
        if result.returncode != 0:
            raise BackupError("QUIESCE_FAILED")

    def unquiesce(self) -> None:
        argv = [*self._base_argv(), "up", "-d", "--no-deps", *self.services]
        result = subprocess.run(
            argv,
            cwd=self.project_dir,
            capture_output=True,
            env=self.env or None,
            check=False,
        )
        if result.returncode != 0:
            raise BackupError("UNQUIESCE_FAILED")


__all__ = ["ComposeQuiescer", "NoopQuiescer", "Quiescer"]
