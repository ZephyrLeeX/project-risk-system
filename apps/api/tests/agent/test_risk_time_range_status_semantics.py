"""PostgreSQL regression: creation-window risk queries are status-agnostic.

"新增风险" is a historical creation fact on ``Risk.detectedAt``: the core
business invariant under test is that a risk does *not* disappear from a
time-window answer after it is resolved, while

* plain ``risk_list`` (no time window) still defaults to current ACTIVE risks,
* an explicit ``status`` argument still filters exactly,
* the dashboard stock metrics (``activeRiskTotal`` …) stay ACTIVE-only while
  ``weeklyNewRiskTotal`` / ``weeklyNewHighRiskTotal`` count every status.

Seeds four risks around the pinned week 2026-08-17 .. 2026-08-24 (Shanghai):

* A — this week Monday, ACTIVE,   in scope,   HIGH
* B — this week Tuesday, RESOLVED, in scope,   HIGH
* C — previous week,     ACTIVE,   in scope,   MEDIUM
* D — this week,         RESOLVED, out of scope, HIGH

The dashboard and history-stability scenarios use their own scope-isolated
users/projects so their aggregates stay exact regardless of the other seeds.
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
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.projects.models import Project
from risk_platform.rbac.models import UserProjectScope
from risk_platform.risks.models import (
    ProjectRiskLevel,
    Risk,
    RiskCategory,
    RiskSourceType,
    RiskStatus,
)
from risk_platform.risks.service import RisksService
from risk_platform.shared.time_ranges import current_week_start
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import WeeklyReportService

ROOT = Path(__file__).resolve().parents[2]
SHANGHAI = ZoneInfo("Asia/Shanghai")
OWNER_AGENT = UUID("00000000-0000-0000-0000-000000000081")
OWNER_DASH = UUID("00000000-0000-0000-0000-000000000082")
OWNER_STAB = UUID("00000000-0000-0000-0000-000000000083")
# Pinned inside the week 2026-08-17 .. 2026-08-24 (Shanghai, Monday-start).
PINNED_NOW = datetime(2026, 8, 21, 14, 0, 0, tzinfo=SHANGHAI)

TITLE_A = "本周新增A-进行中"
TITLE_B = "本周新增B-已解决"
TITLE_C = "上周新增C-进行中"
TITLE_D = "本周新增D-范围外已解决"


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Agent validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t_riskstatus_{uuid.uuid4().hex}"
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
    # The dashboard scenario is seeded against the *real* current week (the
    # dashboard service resolves its own "now") so the aggregates stay exact
    # whenever the suite runs.
    real_week_start = current_week_start(datetime.now(UTC))
    async with transaction(factory) as session:
        for user_id, username in (
            (OWNER_AGENT, "t-riskstatus-agent"),
            (OWNER_DASH, "t-riskstatus-dash"),
            (OWNER_STAB, "t-riskstatus-stab"),
        ):
            session.add(
                User(
                    id=user_id,
                    username=username,
                    passwordHash="not-a-real-password-hash",
                    displayName=username,
                )
            )
        category = RiskCategory(code="T-RISKSTATUS", name="新增状态语义类别")
        session.add(category)
        await session.flush()
        agent_in_scope = Project(name="状态语义-范围内", deliveryOwnerName="范围内")
        agent_out_scope = Project(name="状态语义-范围外", deliveryOwnerName="范围外")
        dash_project = Project(name="状态语义-看板", deliveryOwnerName="看板")
        stab_project = Project(name="状态语义-稳定性", deliveryOwnerName="稳定性")
        session.add_all([agent_in_scope, agent_out_scope, dash_project, stab_project])
        await session.flush()
        session.add(UserProjectScope(projectId=agent_in_scope.id, userId=OWNER_AGENT))
        session.add(UserProjectScope(projectId=dash_project.id, userId=OWNER_DASH))
        session.add(UserProjectScope(projectId=stab_project.id, userId=OWNER_STAB))

        def _risk(
            project: Project,
            title: str,
            level: ProjectRiskLevel,
            status: RiskStatus,
            detected_at: datetime,
        ) -> Risk:
            return Risk(
                projectId=project.id,
                categoryId=category.id,
                title=title,
                description=title,
                level=level,
                status=status,
                sourceType=RiskSourceType.MANUAL,
                dedupeFingerprint=f"t-riskstatus-{project.id}-{title}",
                detectedAt=detected_at,
                resolvedAt=datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
                if status is RiskStatus.RESOLVED
                else None,
            )

        session.add(
            _risk(
                agent_in_scope,
                TITLE_A,
                ProjectRiskLevel.HIGH,
                RiskStatus.ACTIVE,
                datetime(2026, 8, 17, 10, 0, tzinfo=SHANGHAI),
            )
        )
        session.add(
            _risk(
                agent_in_scope,
                TITLE_B,
                ProjectRiskLevel.HIGH,
                RiskStatus.RESOLVED,
                datetime(2026, 8, 18, 10, 0, tzinfo=SHANGHAI),
            )
        )
        session.add(
            _risk(
                agent_in_scope,
                TITLE_C,
                ProjectRiskLevel.MEDIUM,
                RiskStatus.ACTIVE,
                datetime(2026, 8, 12, 10, 0, tzinfo=SHANGHAI),
            )
        )
        session.add(
            _risk(
                agent_out_scope,
                TITLE_D,
                ProjectRiskLevel.HIGH,
                RiskStatus.RESOLVED,
                datetime(2026, 8, 19, 10, 0, tzinfo=SHANGHAI),
            )
        )
        # Dashboard scenario: exactly one ACTIVE HIGH + one RESOLVED HIGH,
        # both detected inside the real current week.
        session.add(
            _risk(
                dash_project,
                "看板-本周新增-进行中",
                ProjectRiskLevel.HIGH,
                RiskStatus.ACTIVE,
                real_week_start + timedelta(hours=1),
            )
        )
        session.add(
            _risk(
                dash_project,
                "看板-本周新增-已解决",
                ProjectRiskLevel.HIGH,
                RiskStatus.RESOLVED,
                real_week_start + timedelta(hours=2),
            )
        )


def _identity(user_id: UUID, username: str) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(user_id),
            username=username,
            displayName=username,
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=["agent.use", "dashboard.view"],
            dataScope="ASSIGNED",
            mustChangePassword=False,
        ),
    )


def _agent_identity() -> SessionIdentity:
    return _identity(OWNER_AGENT, "t-riskstatus-agent")


def _titles(items: Sequence[object]) -> set[str]:
    return {getattr(item, "title", None) or "" for item in items}


def _registry(factory: async_sessionmaker[AsyncSession]) -> AgentToolRegistry:
    return AgentToolRegistry(
        factory,
        DashboardService(factory),
        RisksService(factory),
        TodosService(factory),
        WeeklyReportService(factory),
        clock=lambda: PINNED_NOW,
    )


async def _invoke_titles(
    factory: async_sessionmaker[AsyncSession],
    identity: SessionIdentity,
    arguments: dict[str, object],
) -> tuple[set[str], object]:
    result = await _registry(factory).invoke(
        identity, "risk_list", arguments, trace_id="t-riskstatus-trace"
    )
    data = result.data
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    return (
        {str(item.get("title")) for item in items if isinstance(item, dict)},
        data["total"],
    )


def test_time_window_without_status_returns_active_and_resolved(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Case 1: a creation window is a historical fact — statuses do not filter."""

    async def run() -> None:
        titles, total = await _invoke_titles(
            database, _agent_identity(), {"timeRange": "CURRENT_WEEK"}
        )
        assert titles == {TITLE_A, TITLE_B}
        assert total == 2

    asyncio.run(run())


def test_time_window_with_explicit_active_status(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Case 2: an explicit status is always respected (ACTIVE)."""

    async def run() -> None:
        titles, total = await _invoke_titles(
            database,
            _agent_identity(),
            {"timeRange": "CURRENT_WEEK", "status": "ACTIVE"},
        )
        assert titles == {TITLE_A}
        assert total == 1

    asyncio.run(run())


def test_time_window_with_explicit_resolved_status(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Case 3: an explicit status is always respected (RESOLVED)."""

    async def run() -> None:
        titles, total = await _invoke_titles(
            database,
            _agent_identity(),
            {"timeRange": "CURRENT_WEEK", "status": "RESOLVED"},
        )
        assert titles == {TITLE_B}
        assert total == 1

    asyncio.run(run())


def test_plain_query_still_defaults_to_active(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Case 4: no window, no status → legacy "current risks" (ACTIVE only)."""

    async def run() -> None:
        titles, _total = await _invoke_titles(database, _agent_identity(), {})
        assert titles == {TITLE_A, TITLE_C}

    asyncio.run(run())


def test_level_query_still_defaults_to_active(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Case 5: a plain level filter still means *current* HIGH risks."""

    async def run() -> None:
        titles, _total = await _invoke_titles(database, _agent_identity(), {"level": "HIGH"})
        assert titles == {TITLE_A}

    asyncio.run(run())


def test_resolving_a_risk_keeps_it_in_the_window_answer(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """History stability: the creation fact survives a later status change.

    Also exercises the project-scoped ``list_for_project`` branch, which
    forwards the same historical-intent switch.
    """

    async def run() -> None:
        identity = _identity(OWNER_STAB, "t-riskstatus-stab")
        async with transaction(database) as session:
            project = (
                await session.scalars(
                    select(Project).where(Project.name == "状态语义-稳定性")
                )
            ).first()
            category = (
                await session.scalars(
                    select(RiskCategory).where(RiskCategory.code == "T-RISKSTATUS")
                )
            ).first()
        assert project is not None and category is not None
        async with transaction(database) as session:
            session.add(
                Risk(
                    projectId=project.id,
                    categoryId=category.id,
                    title="稳定性-本周新增",
                    description="稳定性-本周新增",
                    level=ProjectRiskLevel.HIGH,
                    sourceType=RiskSourceType.MANUAL,
                    dedupeFingerprint=f"t-riskstatus-stab-{uuid.uuid4().hex}",
                    detectedAt=datetime(2026, 8, 20, 10, 0, tzinfo=SHANGHAI),
                )
            )
        titles, _total = await _invoke_titles(
            database,
            identity,
            {"timeRange": "CURRENT_WEEK", "projectId": str(project.id)},
        )
        assert "稳定性-本周新增" in titles
        # Resolve the risk, then ask the same historical question again.
        async with transaction(database) as session:
            risk = (
                await session.scalars(
                    select(Risk).where(Risk.title == "稳定性-本周新增")
                )
            ).first()
            assert risk is not None
            risk.status = RiskStatus.RESOLVED
            risk.resolvedAt = datetime.now(UTC)
        titles, _total = await _invoke_titles(
            database,
            identity,
            {"timeRange": "CURRENT_WEEK", "projectId": str(project.id)},
        )
        # The core business invariant: the historical "new this week" fact
        # does not vanish when the risk is later resolved.
        assert "稳定性-本周新增" in titles
        # ... while the plain current-risks query no longer returns it.
        titles, _total = await _invoke_titles(database, identity, {})
        assert "稳定性-本周新增" not in titles

    asyncio.run(run())


def test_dashboard_weekly_new_counts_include_resolved_risks(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """weeklyNew* is a historical creation count; stock metrics stay ACTIVE."""

    async def run() -> None:
        summary = await DashboardService(database).summary(
            _identity(OWNER_DASH, "t-riskstatus-dash")
        )
        # One ACTIVE HIGH + one RESOLVED HIGH, both detected this week.
        assert summary.weeklyNewRiskTotal == 2
        assert summary.weeklyNewHighRiskTotal == 2
        # Stock metrics must not absorb the resolved risk.
        assert summary.activeRiskTotal == 1
        assert summary.highRiskTotal == 1
        assert summary.mediumRiskTotal == 0

    asyncio.run(run())
