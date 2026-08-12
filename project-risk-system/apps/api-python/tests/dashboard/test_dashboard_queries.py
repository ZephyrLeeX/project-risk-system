from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User
from risk_platform.app import AppComposition, create_app
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.dashboard.api import get_dashboard_service
from risk_platform.dashboard.api import router as dashboard_router
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.projects.models import Project, ProjectRiskLevel
from risk_platform.rbac.models import DataScopeType, UserProjectScope
from risk_platform.risks.api import get_risks_service
from risk_platform.risks.api import router as risks_router
from risk_platform.risks.models import Risk, RiskCategory, RiskSourceType
from risk_platform.risks.service import RisksService

ROOT = Path(__file__).resolve().parents[2]
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")


@pytest.fixture(scope="module")
def dashboard_database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL dashboard validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t020_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()
    engine = create_database_engine(f"{sync_url}?options=-csearch_path%3D{schema}")
    factory = create_session_factory(engine)
    try:
        asyncio.run(_seed(factory))
        yield factory
    finally:
        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(factory) as session:
        session.add(
            User(
                id=USER_ID,
                username="t020-user",
                passwordHash="not-a-real-password-hash",
                displayName="T020",
            )
        )
        category = RiskCategory(code="T020", name="T020 类别")
        department = Department(code="T020", name="T020 部门")
        session.add_all([category, department])
        await session.flush()
        owned = Project(
            name="本人负责",
            managerId=USER_ID,
            deliveryOwnerName="本人",
            annualPlanAmount=120,
            remainingAmount=100,
            actualCollectedAmount=20,
            departmentId=department.id,
        )
        assigned = Project(
            name="指定项目",
            deliveryOwnerName="指定",
            annualPlanAmount=240,
            remainingAmount=200,
            actualCollectedAmount=40,
        )
        other = Project(
            name="范围外",
            deliveryOwnerName="范围外",
            annualPlanAmount=360,
            remainingAmount=300,
            actualCollectedAmount=60,
        )
        session.add_all([owned, assigned, other])
        await session.flush()
        session.add(UserProjectScope(projectId=assigned.id, userId=USER_ID))
        for project, title in ((owned, "本人风险"), (assigned, "指定风险"), (other, "范围外风险")):
            session.add(
                Risk(
                    projectId=project.id,
                    categoryId=category.id,
                    title=title,
                    description=title,
                    level=ProjectRiskLevel.HIGH,
                    sourceType=RiskSourceType.MANUAL,
                    dedupeFingerprint=f"t020-{project.id}",
                    detectedAt=datetime.now(UTC),
                )
            )


def _identity(scope: DataScopeType) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(USER_ID),
            username="t020",
            displayName="T020",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=["dashboard.view"],
            dataScope=scope.value,
            mustChangePassword=False,
        ),
    )


async def _client(
    factory: async_sessionmaker[AsyncSession], identity: SessionIdentity
) -> httpx2.AsyncClient:
    risks = RisksService(factory)
    dashboard = DashboardService(factory)

    async def override_identity() -> SessionIdentity:
        return identity

    app = create_app(
        Settings(environment="test", cors_origins=("https://web.internal",)),
        AppComposition(
            routers=(dashboard_router, risks_router),
            dependency_overrides={
                current_identity: override_identity,
                get_dashboard_service: lambda: dashboard,
                get_risks_service: lambda: risks,
            },
        ),
    )
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
    )


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (DataScopeType.ALL, {"本人风险", "指定风险", "范围外风险"}),
        (DataScopeType.OWNED, {"本人风险"}),
        (DataScopeType.ASSIGNED, {"指定风险"}),
        (DataScopeType.OWNED_OR_ASSIGNED, {"本人风险", "指定风险"}),
        (DataScopeType.NONE, set()),
    ],
)
def test_dashboard_routes_apply_all_five_scopes_and_do_not_leak(
    dashboard_database: async_sessionmaker[AsyncSession],
    scope: DataScopeType,
    expected: set[str],
) -> None:
    async def scenario() -> None:
        client = await _client(dashboard_database, _identity(scope))
        try:
            risks = await client.get("/api/risks")
            assert risks.status_code == 200
            payload = risks.json()
            assert payload["code"] == "OK"
            assert {item["title"] for item in payload["data"]["items"]} == expected
            summary = await client.get("/api/dashboard/summary")
            focus = await client.get("/api/dashboard/focus")
            departments = await client.get("/api/dashboard/departments/collections")
            collections = await client.get("/api/dashboard/collections")
            assert summary.status_code == focus.status_code == 200
            assert summary.json()["data"]["activeRiskTotal"] == len(expected)
            assert {item["title"] for item in focus.json()["data"]} <= expected
            assert (
                "范围外风险" not in {item["title"] for item in focus.json()["data"]}
                or scope is DataScopeType.ALL
            )
            assert departments.status_code == collections.status_code == 200
            collection_names = {item["projectName"] for item in collections.json()["data"]["items"]}
            assert collection_names == {
                title.removesuffix("风险").replace("本人", "本人负责").replace("指定", "指定项目")
                for title in expected
            }
            async with dashboard_database() as session:
                outside_id = await session.scalar(select(Risk.id).where(Risk.title == "范围外风险"))
            assert outside_id is not None
            detail = await client.get(f"/api/risks/{outside_id}")
            if scope is DataScopeType.ALL:
                assert detail.status_code == 200
            else:
                assert detail.status_code == 404
                assert detail.json()["code"] == "NOT_FOUND"
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_dashboard_api_parity_includes_summary_focus_options_and_detail(
    dashboard_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        client = await _client(dashboard_database, _identity(DataScopeType.OWNED))
        try:
            risks = (await client.get("/api/risks")).json()["data"]["items"]
            assert len(risks) == 1
            detail = await client.get(f"/api/risks/{risks[0]['id']}")
            options = await client.get("/api/risks/options")
            summary = await client.get("/api/dashboard/summary")
            focus = await client.get("/api/dashboard/focus")
            departments = await client.get("/api/dashboard/departments/collections")
            collections = await client.get("/api/dashboard/collections")
            assert (
                detail.status_code
                == options.status_code
                == summary.status_code
                == focus.status_code
                == departments.status_code
                == collections.status_code
                == 200
            )
            assert risks[0].keys() == {
                "id",
                "projectId",
                "projectName",
                "projectExternalCode",
                "departmentName",
                "projectOwnerName",
                "title",
                "description",
                "evidence",
                "suggestion",
                "level",
                "status",
                "category",
                "sourceType",
                "sourceLabel",
                "reporterName",
                "weekCode",
                "actualCollectedAmountYuan",
                "remainingAmountYuan",
                "detectedAt",
                "updatedAt",
            }
            assert detail.json()["data"].keys() == risks[0].keys() | {
                "resolvedAt",
                "resolvedByName",
                "resolutionReason",
                "sameProjectRisks",
            }
            assert options.json()["data"].keys() == {"categories", "owners"}
            assert options.json()["data"]["owners"] == ["本人"]
            assert summary.json()["data"].keys() == {
                "projectTotal",
                "deliveryProjectTotal",
                "deliveryDepartmentTotal",
                "latestImportBatchCode",
                "latestImportCreatedProjectTotal",
                "activeRiskTotal",
                "highRiskTotal",
                "mediumRiskTotal",
                "lowRiskTotal",
                "unknownRiskTotal",
                "riskProjectTotal",
                "highRiskProjectTotal",
                "weeklyNewRiskTotal",
                "weeklyNewHighRiskTotal",
                "mailAiRiskTotal",
                "manualRiskTotal",
                "excelRiskTotal",
                "litigationRiskTotal",
                "highRiskFocusProjectNames",
                "highRiskPriorityItems",
                "riskRemainingAmountYuan",
                "riskCollectedAmountYuan",
                "riskAmountCompleteProjectTotal",
                "riskAmountMissingProjectTotal",
                "riskCollectionCompletionRate",
                "updatedAt",
                "dataScope",
            }
            assert summary.json()["data"]["dataScope"] == "OWNED"
            department = departments.json()["data"]["items"][0]
            department_detail = await client.get(
                f"/api/dashboard/departments/{department['departmentKey']}/collections"
            )
            collection = collections.json()["data"]["items"][0]
            collection_detail = await client.get(
                f"/api/dashboard/collections/{collection['projectId']}"
            )
            assert department_detail.status_code == collection_detail.status_code == 200
            assert collection["amountSource"] == "PROJECT_LIST"
            assert collection_detail.json()["data"]["statisticalScope"]
            invalid_level = await client.get("/api/dashboard/collections?level=INVALID")
            assert invalid_level.status_code == 422
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_active_risk_lookup_explain_uses_project_status_index(
    dashboard_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        async with dashboard_database() as session:
            project_id = await session.scalar(select(Risk.projectId).limit(1))
            assert project_id is not None
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            plan = await session.execute(
                text(
                    "EXPLAIN (COSTS OFF) "
                    "SELECT id FROM risks "
                    "WHERE \"projectId\" = :project_id AND status = 'ACTIVE'"
                ),
                {"project_id": project_id},
            )
            assert "risks_projectId_status_idx" in "\n".join(row[0] for row in plan)

    asyncio.run(scenario())
