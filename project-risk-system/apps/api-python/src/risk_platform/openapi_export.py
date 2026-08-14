"""Deterministic OpenAPI export for the production FastAPI composition.

T032 freezes the reviewed FastAPI application as the single contract
authority. This module renders the production application's OpenAPI schema in
a stable, byte-reproducible form so frontend TypeScript types can be
regenerated with zero diff from a repository command.

The export only depends on the router/Pydantic-model composition owned by
T040; it never starts the lifespan, opens a database connection, or reads
runtime secrets, so the schema is identical across environments.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from risk_platform.main import app

# ``openapi_export.py`` lives at ``apps/api-python/src/risk_platform/``; the
# inner project root is four parents up. The default path assumes an editable
# (src-tree) install, which is how ``uv run`` installs the package. The
# canonical repository command passes an explicit output path, so non-editable
# installs are also supported.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = REPO_ROOT / "packages" / "contracts" / "openapi" / "openapi.json"


def build_openapi() -> dict[str, Any]:
    """Return the production OpenAPI schema, rebuilt fresh each call.

    The cached schema on the application object is discarded so callers always
    observe the current route/model composition rather than a stale snapshot.
    """

    app.openapi_schema = None
    schema = app.openapi()
    if not isinstance(schema, dict):  # pragma: no cover - FastAPI returns a dict
        raise TypeError("OpenAPI schema must be a JSON object")
    return schema


def serialize_openapi(schema: Mapping[str, Any]) -> str:
    """Serialize the schema to canonical, deterministic JSON text.

    Object keys are sorted and UTF-8 is preserved (``ensure_ascii=False``) so
    the committed artifact is independent of FastAPI/Pydantic insertion order
    and remains reviewable. Array element order (e.g. ``required``) is left
    intact because FastAPI emits it deterministically from model field order.
    """

    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_openapi(output: Path | None = None) -> Path:
    """Write the canonical OpenAPI JSON to ``output`` (default canonical path)."""

    target = output or DEFAULT_OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(serialize_openapi(build_openapi()), encoding="utf-8")
    return target


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry point: ``risk-platform-openapi [output-path]``."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    output: Path | None = None
    if arguments:
        if len(arguments) > 1 or arguments[0] in {"-h", "--help"}:
            print("usage: risk-platform-openapi [output-path]", file=sys.stderr)
            raise SystemExit(2)
        output = Path(arguments[0])
    target = write_openapi(output)
    print(f"OpenAPI exported: {target}")


__all__ = [
    "DEFAULT_OUTPUT",
    "REPO_ROOT",
    "build_openapi",
    "main",
    "serialize_openapi",
    "write_openapi",
]
