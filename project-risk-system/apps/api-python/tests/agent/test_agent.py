from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.risks.service import RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import WeeklyReportService

ROOT = Path(__file__).resolve().parents[2]
OWNER = UUID("00000000-0000-0000-0000-000000000028")
OTHER = UUID("00000000-0000-0000-0000-000000000029")


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Agent validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t028_{uuid.uuid4().hex}"
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
        session.add_all(
            (
                User(
                    id=OWNER,
                    username="t028-owner",
                    passwordHash="not-a-real-password-hash",
                    displayName="T028 Owner",
                ),
                User(
                    id=OTHER,
                    username="t028-other",
                    passwordHash="not-a-real-password-hash",
                    displayName="T028 Other",
                ),
            )
        )


def identity(user_id: UUID = OWNER, permissions: list[str] | None = None) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(user_id),
            username="t028",
            displayName="T028",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=permissions or ["agent.use", "dashboard.view"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


def service(database: async_sessionmaker[AsyncSession]) -> AgentConversationService:
    return AgentConversationService(database, trace_id=lambda: "t028-trace")


def test_tool_registry_is_closed_and_help_is_permission_filtered() -> None:
    registry = AgentToolRegistry(
        DashboardService(None),  # type: ignore[arg-type]
        RisksService(None),  # type: ignore[arg-type]
        TodosService(None),  # type: ignore[arg-type]
        WeeklyReportService(None),  # type: ignore[arg-type]
    )
    assert {item.name for item in registry.help(identity())} == {
        "dashboard_summary",
        "dashboard_focus",
        "risk_list",
        "risk_detail",
        "todo_list",
        "todo_detail",
        "weekly_report",
        "weekly_report_detail",
    }
    assert registry.help(identity(permissions=["agent.use"])) == []

    with pytest.raises(ApiError) as error:
        asyncio.run(registry.invoke(identity(), "arbitrary_sql", {}, trace_id="trace"))
    assert error.value.code == "AGENT_TOOL_NOT_ALLOWED"


def test_conversation_persistence_owner_scope_and_frozen_retention(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        assert created.userMessage.sequence == 1
        assert created.conversation.lastMessageSequence == 1
        assert created.conversation.expiresAt == created.conversation.createdAt + timedelta(days=90)

        continued = await app_service.continue_conversation(
            identity(), created.conversation.id, "请给出风险详情"
        )
        assert continued.userMessage.sequence == 2
        assert not hasattr(continued, "conversation")

        history = await app_service.history(identity(), created.conversation.id)
        assert [message.content for message in history.messages] == [
            "列出本周高风险项目",
            "请给出风险详情",
        ]
        assert history.nextMessageSequence == 3

        with pytest.raises(ApiError) as error:
            await app_service.history(identity(OTHER), created.conversation.id)
        assert error.value.code == "AGENT_CONVERSATION_NOT_FOUND"

    asyncio.run(run())
