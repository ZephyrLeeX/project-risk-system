# Python API workspace

This directory contains the Python 3.12 FastAPI backend workspace. It is managed
independently from the pnpm workspace with [uv](https://docs.astral.sh/uv/).

## Setup and quality checks

Run all commands from this directory:

```bash
uv sync --frozen
uv run ruff check .
uv run mypy .
uv run pytest
```

## Repeatable Seed

After Alembic has upgraded an empty PostgreSQL database, provide
`INITIAL_ADMIN_PASSWORD` through the process environment and run:

```bash
uv run python -m risk_platform.seed
```

Optional settings are `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_DISPLAY_NAME`, and
`PASSWORD_MIN_LENGTH` (minimum 12). The password is never printed. Re-running the
command refreshes approved reference data without replacing the administrator's
password hash or `mustChangePassword` state.

The packages under `src/risk_platform/` establish module ownership only. Runtime
application composition, routes, persistence, and domain behavior are introduced
by their owning implementation tasks.
