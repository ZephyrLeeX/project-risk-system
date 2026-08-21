"""PostgreSQL regression: risk_list time filtering and DataScope interact safely.

Seeds risks at every position around a fixed ``[start, end)`` window — before,
at the start, inside, at the end (exclusive bound), and after — under two
projects, only one of which is inside the querying identity's ASSIGNED data
scope.  Verifies that:

* the half-open ``[start, end)`` boundary on ``Risk.detectedAt`` holds exactly;
* the time filter cannot bypass the DataScope predicate (out-of-scope rows
  inside the window are still excluded);
* the Agent ``risk_list`` tool path (preset against an injected clock) returns
  the same rows as the direct service query.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.projects.models import Project
from risk_platform.rbac.models import UserProjectScope
from risk_platform.risks.models import ProjectRiskLevel, Risk, RiskCategory, RiskSourceType
from risk_platform.risks.schemas import RiskQuery
from risk_platform.risks.service import RisksService
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import WeeklyReportService

ROOT = Path(__file__).resolve().parents[2]
SHANGHAI = ZoneInfo("Asia/Shanghai")
OWNER = UUID("00000000-0000-0000-0000-000000000071")
# Fixed window: Monday 2026-08-17 00:00 .. Monday 2026-08-24 00:00 (Shanghai),
# resolved server-side from CURRENT_WEEK at the pinned clock.
PINNED_NOW = datetime(2026, 8, 21, 14, 0, 0, tzinfo=SHANGHAI)
WINDOW_START = datetime(2026, 8, 17, tzinfo=SHANGHAI)
WINDOW_END = datetime(2026, 8, 24, tzinfo=SHANGHAI)

# (key, detectedAt) covering every boundary position.
BOUNDARY_INSTANTS: tuple[tuple[str, datetime], ...] = (
    ("before", WINDOW_START - timedelta(hours=1)),
    ("at_start", WINDOW_START),
    ("inside", WINDOW_START + timedelta(days=2, hours=3)),
    ("at_end", WINDOW_END),
    ("after", WINDOW_END + timedelta(hours=1)),
)
EXPECTED_IN_WINDOW = ("at_start", "inside")


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Agent validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t_timerange_{uuid.uuid4().hex}"
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
                id=OWNER,
                username="t-timerange-owner",
                passwordHash="not-a-real-password-hash",
                displayName="T TimeRange Owner",
            )
        )
        category = RiskCategory(code="T-TIMERANGE", name="时间范围类别")
        session.add(category)
        await session.flush()
        in_scope = Project(name="授权范围内项目", deliveryOwnerName="范围内")
        out_of_scope = Project(name="范围外项目", deliveryOwnerName="范围外")
        session.add_all([in_scope, out_of_scope])
        await session.flush()
        session.add(UserProjectScope(projectId=in_scope.id, userId=OWNER))
        for project in (in_scope, out_of_scope):
            for key, detected_at in BOUNDARY_INSTANTS:
                session.add(
                    Risk(
                        projectId=project.id,
                        categoryId=category.id,
                        title=f"{project.name}-{key}",
                        description=key,
                        level=ProjectRiskLevel.HIGH,
                        sourceType=RiskSourceType.MANUAL,
                        dedupeFingerprint=f"t-timerange-{project.id}-{key}",
                        detectedAt=detected_at,
                        # updatedAt differs from detectedAt so a filter bug on
                        # the wrong column becomes visible.
                        updatedAt=WINDOW_END + timedelta(days=30),
                    )
                )


def _assigned_identity() -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(OWNER),
            username="t-timerange",
            displayName="T TimeRange",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=["agent.use", "dashboard.view"],
            dataScope="ASSIGNED",
            mustChangePassword=False,
        ),
    )


def _titles(items: Sequence[object]) -> set[str]:
    return {getattr(item, "title", None) or "" for item in items}


def test_service_time_window_is_half_open_and_scope_safe(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        service = RisksService(database)
        page = await service.list(
            _assigned_identity(),
            RiskQuery(page=1, pageSize=20),
            detected_from=WINDOW_START,
            detected_to=WINDOW_END,
        )
        titles = _titles(page.items)
        # Exactly the in-window, in-scope rows: start inclusive, end exclusive.
        assert titles == {"授权范围内项目-at_start", "授权范围内项目-inside"}
        assert page.total == 2
        # The same query without a window still hides the out-of-scope project.
        unfiltered = await service.list(
            _assigned_identity(), RiskQuery(page=1, pageSize=50)
        )
        assert all("范围外项目" not in title for title in _titles(unfiltered.items))
        assert unfiltered.total == len(BOUNDARY_INSTANTS)

    asyncio.run(run())


def test_agent_risk_list_preset_applies_window_and_scope(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        registry = AgentToolRegistry(
            database,
            DashboardService(database),
            RisksService(database),
            TodosService(database),
            WeeklyReportService(database),
            clock=lambda: PINNED_NOW,
        )
        result = await registry.invoke(
            _assigned_identity(),
            "risk_list",
            {"level": "HIGH", "timeRange": "CURRENT_WEEK"},
            trace_id="t-timerange-trace",
        )
        data = result.data
        assert isinstance(data, dict)
        items = data["items"]
        assert isinstance(items, list)
        titles = {str(item.get("title")) for item in items if isinstance(item, dict)}
        assert titles == {"授权范围内项目-at_start", "授权范围内项目-inside"}
        assert data["total"] == 2
        # detectedAt is returned so the Agent can answer with real dates.
        for item in items:
            assert isinstance(item, dict) and item.get("detectedAt")

    asyncio.run(run())


def test_previous_week_preset_excludes_the_current_week(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        registry = AgentToolRegistry(
            database,
            DashboardService(database),
            RisksService(database),
            TodosService(database),
            WeeklyReportService(database),
            clock=lambda: PINNED_NOW,
        )
        result = await registry.invoke(
            _assigned_identity(),
            "risk_list",
            {"timeRange": "PREVIOUS_WEEK"},
            trace_id="t-timerange-trace",
        )
        data = result.data
        assert isinstance(data, dict)
        items = data["items"]
        assert isinstance(items, list)
        titles = {str(item.get("title")) for item in items if isinstance(item, dict)}
        # The only seeded instant inside 2026-08-10 .. 2026-08-17 is the
        # "before" row (start minus one hour); the current-week rows are out.
        assert titles == {"授权范围内项目-before"}
        assert data["total"] == 1

    asyncio.run(run())
