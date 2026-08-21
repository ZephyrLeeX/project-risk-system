"""T032 OpenAPI authority export tests: determinism, envelope and surface invariants."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from risk_platform.admin.overview.schemas import (
    AdminOverview,
    AttentionItem,
    HealthItem,
    OverviewLink,
    RecentAuditItem,
    UnavailableSection,
)
from risk_platform.agent.schemas import (
    AgentConfirmationResponse,
    AgentConversationEnvelope,
    AgentConversationHistory,
    AgentConversationResponse,
    AgentHelpResponse,
    AgentMessageEnvelope,
    AgentMessagePage,
    AgentMessageResponse,
    AgentToolHelp,
    AgentToolResult,
)
from risk_platform.openapi_export import (
    DEFAULT_OUTPUT,
    build_openapi,
    main,
    serialize_openapi,
    write_openapi,
)
from risk_platform.weekly_reports.schemas import (
    WeeklyProject,
    WeeklyProjectDetail,
    WeeklyProjectSummary,
    WeeklyReportItemResponse,
    WeeklyReportResponse,
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


def test_provider_v2_additive_surface_and_secret_boundary() -> None:
    schema = build_openapi()
    paths = schema["paths"]
    assert "/api/admin/ai-services" in paths
    assert "/api/admin/ai-provider-v2/accounts" in paths
    assert "/api/admin/ai-provider-v2/accounts/{account_id}/models" in paths
    assert "/api/admin/ai-provider-v2/accounts/{account_id}/models/discover" in paths
    components = schema["components"]["schemas"]
    account = components["ProviderAccountResponse"]["properties"]
    assert "maskedKey" in account
    assert "apiKey" not in account
    assert "encryptedApiKey" not in account
    create = components["CreateProviderAccountRequest"]["properties"]
    assert create["providerType"]["const"] == "DEEPSEEK_OFFICIAL"
    assert "endpoint" not in create


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


# ---------------------------------------------------------------------------
# T045: _Contract serialization-mode schema fidelity.
#
# The duplicated _Contract wildcard ``field_serializer("*", check_fields=False)``
# previously declared a ``-> object`` return type, which made Pydantic replace
# every field's serialization-mode JSON schema with ``object``. FastAPI builds
# response-model OpenAPI schemas in serialization mode, so every _Contract field
# collapsed to ``{"title": ...}`` (-> ``unknown`` in the generated TypeScript).
# The fix omits the return annotation so Pydantic keeps each field's declared
# type while the body still reformats datetimes. These tests pin that fidelity.
# ---------------------------------------------------------------------------


def _degraded_fields(component: dict[str, Any]) -> list[str]:
    """Property names whose serialization schema lost its type information.

    A field degraded by the old ``-> object`` serializer kept only ``{"title": ...}``
    with no ``type``/``enum``/``anyOf``/``allOf``/``$ref``.
    """
    degraded: list[str] = []
    for name, field in component.get("properties", {}).items():
        if isinstance(field, dict) and not (set(field) - {"title"}):
            degraded.append(name)
    return degraded


# Every _Contract-derived model whose serialization-mode schema must express real
# field types. ``AgentToolResult`` is not an HTTP response model, so it is absent
# from the OpenAPI authority and is checked at the model level here.
CONTRACT_FIDELITY_MODELS: list[type[BaseModel]] = [
    AdminOverview,
    HealthItem,
    AttentionItem,
    RecentAuditItem,
    UnavailableSection,
    OverviewLink,
    WeeklyReportResponse,
    WeeklyProjectDetail,
    WeeklyReportItemResponse,
    WeeklyProjectSummary,
    WeeklyProject,
    AgentConversationResponse,
    AgentConfirmationResponse,
    AgentConversationEnvelope,
    AgentMessageEnvelope,
    AgentConversationHistory,
    AgentMessagePage,
    AgentToolHelp,
    AgentHelpResponse,
    AgentMessageResponse,
    AgentToolResult,
]


def test_contract_models_express_real_serialization_field_types() -> None:
    """No _Contract field may degrade to ``{"title": ...}`` (unknown)."""
    failures: list[str] = []
    for model in CONTRACT_FIDELITY_MODELS:
        schema = model.model_json_schema(mode="serialization")
        degraded = _degraded_fields(schema)
        if degraded:
            failures.append(f"{model.__name__}: degraded {degraded}")
        if schema.get("additionalProperties") is not False:
            failures.append(
                f"{model.__name__}: additionalProperties={schema.get('additionalProperties')!r}"
            )
    assert not failures, "serialization-mode schema fidelity broken:\n  " + "\n  ".join(failures)


def test_openapi_authority_exposes_real_contract_field_types() -> None:
    """The frozen OpenAPI authority carries real types for _Contract schemas."""
    components = build_openapi()["components"]["schemas"]
    failures: list[str] = []
    for model in CONTRACT_FIDELITY_MODELS:
        if model is AgentToolResult:
            continue  # not an HTTP response model -> absent from authority
        name = model.__name__
        assert name in components, f"{name} missing from OpenAPI authority"
        component = components[name]
        degraded = _degraded_fields(component)
        if degraded:
            failures.append(f"{name}: degraded {degraded}")
        if component.get("additionalProperties") is not False:
            failures.append(
                f"{name}: additionalProperties={component.get('additionalProperties')!r}"
            )
    assert not failures, "OpenAPI authority schema fidelity broken:\n  " + "\n  ".join(failures)


def test_contract_field_type_details_are_present() -> None:
    """datetime -> date-time, enums -> enum, nested models -> $ref."""
    components = build_openapi()["components"]["schemas"]
    health = components["HealthItem"]["properties"]
    assert health["checkedAt"].get("format") == "date-time"
    assert health["checkedAt"].get("type") == "string"
    assert "enum" in health["key"]
    assert "enum" in health["status"]
    link = health["link"]
    assert "$ref" in link or any("$ref" in opt for opt in link.get("anyOf", []))
    recent = components["RecentAuditItem"]["properties"]
    assert "$ref" in recent["module"]
    weekly = components["WeeklyReportResponse"]["properties"]
    assert weekly["freshnessDeadline"].get("format") == "date-time"
    confirm = components["AgentConfirmationResponse"]["properties"]
    assert confirm["completedAt"].get("format") == "date-time"


def test_contract_datetime_runtime_json_is_utc_milliseconds_with_z() -> None:
    """Runtime JSON behavior is unchanged: UTC RFC 3339 ms + Z, json-only."""
    moment = datetime(2026, 8, 14, 12, 0, 0, 123456, tzinfo=UTC)
    item = HealthItem(
        key="API",
        label="API",
        status="HEALTHY",
        checkedAt=moment,
        summary="ok",
        code=None,
        link=None,
    )
    assert json.loads(item.model_dump_json())["checkedAt"] == "2026-08-14T12:00:00.123Z"
    # ``when_used="json"`` leaves Python ``model_dump`` returning datetime objects.
    assert isinstance(item.model_dump()["checkedAt"], datetime)


def test_openapi_surface_contains_provider_v2_additive_paths() -> None:
    """T051 extends the T050 interaction contract; T052 adds the runtime-restore
    POST /conversations/{id}/cancel path plus the AgentConversationRuntime schema
    (and its ApiResponse wrapper)."""
    schema = build_openapi()
    assert len(schema["paths"]) == 111
    assert len(schema["components"]["schemas"]) == 286
