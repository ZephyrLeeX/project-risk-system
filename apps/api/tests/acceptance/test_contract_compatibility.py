"""T037 — cross-module runtime contract compatibility.

Proves the approved ``/api`` contract holds uniformly across every migrated
module at runtime: the ``ApiResponse`` envelope (``code``/``message``/
``data``/``traceId``), the ``PaginatedResponse`` shape, the fixed error codes
for 401/403/404/422, the ``x-trace-id`` header, and Cookie semantics on the
real session login path. This is the runtime complement of T032's frozen
OpenAPI authority.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from risk_platform.rbac.models import DataScopeType

from .conftest import AcceptanceHarness

SUCCESS_ENDPOINTS = (
    "/api/health",
    "/api/risks",
    "/api/risks/options",
    "/api/dashboard/summary",
    "/api/dashboard/focus",
    "/api/dashboard/departments/collections",
    "/api/dashboard/collections",
    "/api/admin/audit-logs/summary",
    "/api/admin/audit-logs/options",
    "/api/admin/audit-logs/integrity",
    "/api/admin/overview",
    "/api/agent/help",
    "/api/todos",
)


def _assert_envelope(payload: dict[str, object], *, code: str = "OK") -> None:
    assert set(payload) == {"code", "message", "data", "traceId"}
    assert payload["code"] == code
    assert isinstance(payload["message"], str) and payload["message"]
    assert isinstance(payload["traceId"], str)
    assert re.fullmatch(r"[0-9a-f-]{36}", payload["traceId"])


def _assert_envelope_shape(payload: dict[str, object]) -> None:
    """Assert the envelope keys/trace exist without requiring ``code == OK``."""

    assert set(payload) == {"code", "message", "data", "traceId"}
    assert isinstance(payload["message"], str) and payload["message"]
    assert isinstance(payload["traceId"], str)
    assert re.fullmatch(r"[0-9a-f-]{36}", payload["traceId"])


def test_success_envelope_and_trace_id_are_uniform_across_modules(
    acceptance: AcceptanceHarness,
) -> None:
    async def scenario() -> None:
        identity = acceptance.full_identity()
        app = acceptance.build_app(identity=identity)
        async for client in acceptance.client(app):
            for path in SUCCESS_ENDPOINTS:
                response = await client.get(path)
                assert response.status_code == 200, f"{path}: {response.text}"
                assert response.headers["x-trace-id"] == response.json()["traceId"]
                _assert_envelope(response.json())

    asyncio.run(scenario())


def test_paginated_response_shape_is_uniform(acceptance: AcceptanceHarness) -> None:
    async def scenario() -> None:
        identity = acceptance.full_identity()
        app = acceptance.build_app(identity=identity)
        async for client in acceptance.client(app):
            risks = await client.get("/api/risks", params={"page": 1, "pageSize": 10})
            assert risks.status_code == 200
            data = risks.json()["data"]
            assert set(data) == {"items", "page", "pageSize", "total"}
            assert data["page"] == 1 and data["pageSize"] == 10
            assert data["total"] == len(data["items"]) == 3

            audit = await client.get("/api/admin/audit-logs", params={"page": 1, "pageSize": 10})
            assert audit.status_code == 200
            audit_data = audit.json()["data"]
            assert {"items", "page", "pageSize", "total"} <= set(audit_data)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (404, "NOT_FOUND"), (422, "VALIDATION_ERROR")],
)
def test_error_envelope_uses_fixed_codes_and_trace_header(
    acceptance: AcceptanceHarness, status: int, code: str
) -> None:
    async def scenario() -> None:
        app = acceptance.build_app(identity=acceptance.identity_for("PROJECT_MANAGER"))
        async for client in acceptance.client(app):
            if status == 401:
                unauthenticated = acceptance.build_app()
                async for anon in acceptance.client(unauthenticated):
                    response = await anon.get("/api/risks")
            elif status == 403:
                # PROJECT_MANAGER lacks admin.audit.export; the export endpoint
                # is origin-gated and permission-gated -> 403 before body work.
                response = await client.post(
                    "/api/admin/audit-logs/export",
                    headers={"origin": "https://web.internal"},
                    json={},
                )
            elif status == 404:
                response = await client.get("/api/risks/00000000-0000-0000-0000-000000000000")
            else:
                response = await client.post("/api/auth/login", json={"unexpected": "field"})
            assert response.status_code == status, response.text
            payload = response.json()
            assert payload["code"] == code
            assert isinstance(payload["message"], str) and payload["message"]
            assert payload["data"] is None or isinstance(payload["data"], dict)
            assert response.headers["x-trace-id"] == payload["traceId"]

    asyncio.run(scenario())


def test_weekly_report_stale_response_keeps_envelope_contract(
    acceptance: AcceptanceHarness,
) -> None:
    async def scenario() -> None:
        # A freshly seeded schema has no materialized weekly aggregate, so the
        # approved ADR 0021 stale/rebuild response is returned. It must still
        # honour the runtime envelope and trace header.
        app = acceptance.build_app(identity=acceptance.full_identity())
        async for client in acceptance.client(app):
            response = await client.get("/api/weekly-reports/current")
            assert response.status_code in (200, 503)
            _assert_envelope_shape(response.json())
            assert response.headers["x-trace-id"] == response.json()["traceId"]

    asyncio.run(scenario())


def test_real_login_sets_compliant_session_cookie(acceptance: AcceptanceHarness) -> None:
    async def scenario() -> None:
        # No identity override: exercise the real cookie/session path. Production
        # environment so the Secure attribute and full origin validation apply.
        app = acceptance.build_app(production=True)
        async for client in acceptance.client(app):
            login = await acceptance.login(client, "PROJECT_MANAGER")
            assert login.status_code == 200
            cookie = login.headers["set-cookie"]
            assert "project_risk_session=" in cookie
            for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
                assert attribute in cookie, attribute
            assert re.search(r"expires=", cookie, re.IGNORECASE)
            _assert_envelope(login.json())
            assert login.json()["data"]["user"]["mustChangePassword"] is False

            session = await client.get("/api/auth/session")
            assert session.status_code == 200
            assert session.json()["data"]["expiresAt"] == login.json()["data"]["expiresAt"]

    asyncio.run(scenario())


def test_data_scope_none_returns_empty_page_without_envelope_change(
    acceptance: AcceptanceHarness,
) -> None:
    async def scenario() -> None:
        identity = acceptance.identity_for("PROJECT_MANAGER", scope=DataScopeType.NONE)
        app = acceptance.build_app(identity=identity)
        async for client in acceptance.client(app):
            response = await client.get("/api/risks")
            assert response.status_code == 200
            _assert_envelope(response.json())
            data = response.json()["data"]
            assert data["items"] == []
            assert data["total"] == 0

    asyncio.run(scenario())
