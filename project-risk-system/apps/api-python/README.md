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

The packages under `src/risk_platform/` establish module ownership only. Runtime
application composition, routes, persistence, and domain behavior are introduced
by their owning implementation tasks.
