"""``pg_dump`` / ``pg_restore`` invocation (ADR 0031 §2, §3, §12).

PostgreSQL is captured as a single-snapshot ``pg_dump -Fc`` custom-format dump
(the postgres:16-alpine image provides the binaries; the Python orchestrator
invokes them via a configurable runner prefix so the same code runs in the
isolated drill and in production). Restore uses ``pg_restore`` into an isolated
empty database. No WAL archiving / PITR (ADR 0031 §1, T036 out-of-scope).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from risk_backup.errors import BackupError


@dataclass(frozen=True, slots=True)
class PgConnection:
    """Connection parameters applied to pg_dump / pg_restore / psql.

    ``socket_dir`` selects a local Unix-socket connection (the postgres
    container's trust-auth local socket when the binary runs inside that
    container via ``runner``); otherwise TCP with ``host``/``port``/``password``.
    """

    username: str
    database: str
    host: str | None = None
    port: int | None = None
    socket_dir: str | None = None
    password: str | None = None

    def argv(self, *, database: str | None = None) -> list[str]:
        args: list[str] = []
        if self.socket_dir is not None:
            args += ["-h", self.socket_dir]
        elif self.host is not None:
            args += ["-h", self.host]
            if self.port is not None:
                args += ["-p", str(self.port)]
        args += ["-U", self.username, "-d", database or self.database]
        return args

    def env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        return env


@dataclass(frozen=True, slots=True)
class PgDumper:
    connection: PgConnection
    runner: list[str] = field(default_factory=list)  # argv prefix, e.g. docker exec -i <c>
    pg_dump_bin: str = "pg_dump"
    pg_restore_bin: str = "pg_restore"

    def dump(self, out_path: Path) -> None:
        """Run ``pg_dump -Fc`` streaming the custom-format dump to ``out_path``."""

        argv = [*self.runner, self.pg_dump_bin, *self.connection.argv(), "-Fc"]
        try:
            with open(out_path, "wb") as handle:
                result = subprocess.run(
                    argv,
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    env={**_clean_env(), **self.connection.env()},
                    check=False,
                )
        except OSError as exc:
            raise BackupError("PG_DUMP_INVOKE_FAILED", cause=exc) from exc
        if result.returncode != 0:
            raise BackupError("PG_DUMP_FAILED")

    def restore(self, dump_path: Path, *, target_database: str) -> None:
        """Run ``pg_restore`` from ``dump_path`` into the isolated target DB."""

        argv = [
            *self.runner,
            self.pg_restore_bin,
            *self.connection.argv(database=target_database),
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
        ]
        try:
            with open(dump_path, "rb") as handle:
                result = subprocess.run(
                    argv,
                    stdin=handle,
                    capture_output=True,
                    env={**_clean_env(), **self.connection.env()},
                    check=False,
                )
        except OSError as exc:
            raise BackupError("PG_RESTORE_INVOKE_FAILED", cause=exc) from exc
        if result.returncode != 0:
            raise BackupError("PG_RESTORE_FAILED")


def _clean_env() -> dict[str, str]:
    import os

    # pg_dump/pg_restore must not inherit a PGPASSWORD for the wrong role; the
    # connection env supplies the intended one.
    env = {k: v for k, v in os.environ.items() if k != "PGPASSWORD"}
    return env


__all__ = ["PgConnection", "PgDumper"]
