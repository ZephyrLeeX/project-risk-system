"""T032 OpenAPI authority export tests: determinism, envelope and surface invariants."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from risk_platform.openapi_export import (
    DEFAULT_OUTPUT,
    build_openapi,
    main,
    serialize_openapi,
    write_openapi,
)

ENVELOPE_FIELDS = {"code", "message", "data", "traceId"}


def _envelope_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every response envelope component (``ApiResponse[...]``)."""

    components = schema.get("components", {}).get("schemas", {})
    return [
        component
        for component in components.values()
        if isinstance(component, dict)
        and set(component.get("required", [])) == ENVELOPE_FIELDS
        and set(component.get("properties", {})).issuperset(ENVELOPE_FIELDS)
    ]


def test_build_openapi_exposes_stable_top_level_surface() -> None:
    schema = build_openapi()

    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "Project Risk Management API"
    assert schema["info"]["version"] == version("risk-platform-api")
    assert "paths" in schema and schema.get("paths")
    assert "components" in schema and "schemas" in schema["components"]


def test_serialize_is_deterministic_across_rebuilds() -> None:
    first = serialize_openapi(build_openapi())
    second = serialize_openapi(build_openapi())

    assert first == second


def test_serialize_is_independent_of_object_key_order() -> None:
    shuffled = {
        "zeta": {"y": 1, "x": 2},
        "alpha": [3, 2, 1],
        "nested": {"b": 1, "a": 0},
    }
    ordered = {
        "alpha": [3, 2, 1],
        "zeta": {"x": 2, "y": 1},
        "nested": {"a": 0, "b": 1},
    }

    assert serialize_openapi(shuffled) == serialize_openapi(ordered)


def test_envelope_contract_is_preserved() -> None:
    schema = build_openapi()

    envelopes = _envelope_schemas(schema)
    assert envelopes, "no ApiResponse envelope schema component found"

    for envelope in envelopes:
        assert set(envelope["required"]) == ENVELOPE_FIELDS
        assert set(envelope["properties"]) == ENVELOPE_FIELDS


def test_operations_are_unique_and_path_prefixed() -> None:
    schema = build_openapi()

    operations: list[tuple[str, str]] = []
    for path, item in schema["paths"].items():
        assert path.startswith("/api/"), path
        for method, operation in item.items():
            if method in {"parameters"}:
                continue
            assert method in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
            }, (path, method)
            assert "responses" in operation, (method, path)
            operations.append((method.upper(), path))

    assert len(operations) == len(set(operations)), "duplicate (method, path) operation"


def test_write_openapi_emits_canonical_file(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    written = write_openapi(target)

    assert written == target
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # The committed artifact must round-trip and match a fresh build exactly.
    assert text == serialize_openapi(build_openapi())
    assert json.loads(text)["openapi"].startswith("3.")


def test_default_output_points_at_contracts_package() -> None:
    assert DEFAULT_OUTPUT.name == "openapi.json"
    assert DEFAULT_OUTPUT.parent.name == "openapi"
    assert DEFAULT_OUTPUT.parent.parent.name == "contracts"


def test_main_writes_explicit_path(tmp_path: Path) -> None:
    target = tmp_path / "out.json"

    main([str(target)])

    assert target.exists()
    assert target.read_text(encoding="utf-8") == serialize_openapi(build_openapi())


def test_main_rejects_extra_arguments(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "a.json"), str(tmp_path / "b.json")])

    assert excinfo.value.code == 2
