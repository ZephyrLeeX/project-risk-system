"""T037 — four-role / five-scope authorization matrix.

Proves the approved RBAC boundary (Design §11, ADR 0019) holds across modules
with both positive and negative cases: each default role reaches only the
endpoints its permissions grant, and each of the five project data scopes
returns exactly the authorized active projects with no cross-scope leakage.
"""

from __future__ import annotations

import asyncio

import pytest

from risk_platform.rbac.models import DataScopeType

from .conftest import AcceptanceHarness

# (role, endpoint, expected status). Positive and negative cases per role.
# Permission map (seed.ROLES): SYSTEM_ADMIN has dashboard.view + all admin.*
# but no agent/risk/mailbox; RISK_ADMIN has dashboard/agent/risk/mailbox but no
# admin.*; PROJECT_MANAGER has dashboard/agent/risk.report/resolve only;
# VIEWER_AUDITOR has dashboard/agent/admin.audit.view only.
ROLE_ENDPOINT_MATRIX = (
    ("RISK_ADMIN", "/api/dashboard/summary", 200),
    ("RISK_ADMIN", "/api/risks", 200),
    ("RISK_ADMIN", "/api/agent/help", 200),
    ("RISK_ADMIN", "/api/mailbox/sync-summary", 200),
    ("RISK_ADMIN", "/api/admin/audit-logs/summary", 403),
    ("RISK_ADMIN", "/api/admin/users", 403),
    ("PROJECT_MANAGER", "/api/dashboard/summary", 200),
    ("PROJECT_MANAGER", "/api/risks", 200),
    ("PROJECT_MANAGER", "/api/agent/help", 200),
    ("PROJECT_MANAGER", "/api/admin/audit-logs/summary", 403),
    ("PROJECT_MANAGER", "/api/admin/users", 403),
    ("PROJECT_MANAGER", "/api/mailbox/sync-summary", 403),
    ("VIEWER_AUDITOR", "/api/dashboard/summary", 200),
    ("VIEWER_AUDITOR", "/api/agent/help", 200),
    ("VIEWER_AUDITOR", "/api/admin/audit-logs/summary", 200),
    ("VIEWER_AUDITOR", "/api/admin/audit-logs/integrity", 200),
    ("VIEWER_AUDITOR", "/api/admin/users", 403),
    ("VIEWER_AUDITOR", "/api/mailbox/sync-summary", 403),
    ("SYSTEM_ADMIN", "/api/dashboard/summary", 200),
    ("SYSTEM_ADMIN", "/api/risks", 200),
    ("SYSTEM_ADMIN", "/api/admin/users", 200),
    ("SYSTEM_ADMIN", "/api/admin/ai-services", 200),
    ("SYSTEM_ADMIN", "/api/agent/help", 403),
    ("SYSTEM_ADMIN", "/api/mailbox/sync-summary", 403),
)


@pytest.mark.parametrize(("role", "endpoint", "expected"), ROLE_ENDPOINT_MATRIX)
def test_four_role_permission_matrix(
    acceptance: AcceptanceHarness, role: str, endpoint: str, expected: int
) -> None:
    async def scenario() -> None:
        app = acceptance.build_app(identity=acceptance.identity_for(role))
        async for client in acceptance.client(app):
            response = await client.get(endpoint)
            assert response.status_code == expected, f"{role} {endpoint}: {response.text}"

    asyncio.run(scenario())


SCOPE_EXPECTATIONS: tuple[tuple[DataScopeType, set[str]], ...] = (
    (DataScopeType.ALL, {"验收-本人负责项目", "验收-授权项目", "验收-范围外项目"}),
    (DataScopeType.OWNED, {"验收-本人负责项目"}),
    (DataScopeType.ASSIGNED, {"验收-授权项目"}),
    (DataScopeType.OWNED_OR_ASSIGNED, {"验收-本人负责项目", "验收-授权项目"}),
    (DataScopeType.NONE, set()),
)


@pytest.mark.parametrize(("scope", "expected_projects"), SCOPE_EXPECTATIONS)
def test_five_scope_filtering_returns_only_authorized_active_projects(
    acceptance: AcceptanceHarness, scope: DataScopeType, expected_projects: set[str]
) -> None:
    async def scenario() -> None:
        # PROJECT_MANAGER owns one project and is assigned to another.
        identity = acceptance.identity_for("PROJECT_MANAGER", scope=scope)
        app = acceptance.build_app(identity=identity)
        async for client in acceptance.client(app):
            risks = await client.get("/api/risks")
            assert risks.status_code == 200
            titles = {item["title"] for item in risks.json()["data"]["items"]}
            assert titles == {f"风险-{name}" for name in expected_projects}

            summary = await client.get("/api/dashboard/summary")
            assert summary.status_code == 200
            assert summary.json()["data"]["activeRiskTotal"] == len(expected_projects)

            focus = await client.get("/api/dashboard/focus")
            assert focus.status_code == 200
            focus_titles = {item["title"] for item in focus.json()["data"]}
            assert focus_titles <= {f"风险-{name}" for name in expected_projects}

    asyncio.run(scenario())


def test_archived_projects_are_excluded_from_all_scopes(acceptance: AcceptanceHarness) -> None:
    async def scenario() -> None:
        from risk_platform.db import transaction
        from risk_platform.projects.models import Project, ProjectStatus

        # Archive the owned project; even ALL scope must not list it.
        async with transaction(acceptance.env.factory) as session:
            owned = await session.get(Project, acceptance.env.seed.projects["owned"])
            assert owned is not None
            owned.status = ProjectStatus.ARCHIVED

        identity = acceptance.identity_for("PROJECT_MANAGER", scope=DataScopeType.ALL)
        app = acceptance.build_app(identity=identity)
        async for client in acceptance.client(app):
            risks = await client.get("/api/risks")
            assert risks.status_code == 200
            titles = {item["title"] for item in risks.json()["data"]["items"]}
            assert "风险-验收-本人负责项目" not in titles
            assert risks.json()["data"]["total"] == 2

    asyncio.run(scenario())


def test_out_of_scope_risk_detail_is_not_accessible(acceptance: AcceptanceHarness) -> None:
    async def scenario() -> None:
        # VIEWER_AUDITOR is assigned only to the "assigned" project; the risk on
        # the "other" project must be invisible (404, not 200 and not 403-leaky).
        identity = acceptance.identity_for("VIEWER_AUDITOR")
        app = acceptance.build_app(identity=identity)
        other_risk_title = "风险-验收-范围外项目"
        assigned_risk_title = "风险-验收-授权项目"
        async for client in acceptance.client(app):
            risks = await client.get("/api/risks")
            listed = {item["id"] for item in risks.json()["data"]["items"]}
            by_title = {item["title"]: item["id"] for item in risks.json()["data"]["items"]}
            assert by_title.keys() == {"风险-验收-授权项目"}
            assigned_id = by_title[assigned_risk_title]

            visible = await client.get(f"/api/risks/{assigned_id}")
            assert visible.status_code == 200

            # The other-project risk id is not listed; fetching it directly must
            # be refused. Discover its id through an ALL-scope view first.
            admin_app = acceptance.build_app(identity=acceptance.full_identity())
            async for admin in acceptance.client(admin_app):
                admin_risks = await admin.get("/api/risks")
                other_id = next(
                    item["id"]
                    for item in admin_risks.json()["data"]["items"]
                    if item["title"] == other_risk_title
                )
            assert other_id not in listed
            forbidden = await client.get(f"/api/risks/{other_id}")
            assert forbidden.status_code == 404

    asyncio.run(scenario())
