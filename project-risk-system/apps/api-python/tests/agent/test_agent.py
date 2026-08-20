from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.models import (
    AgentConversation,
    AgentMessage,
    AgentMessageRole,
)
from risk_platform.agent.schemas import (
    AgentRiskListPage,
    ProjectDetailToolResponse,
    ProjectListToolResponse,
    RiskCategoryListToolResponse,
)
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry, AgentToolResultTypeError
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.schemas import DashboardSummary
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.risks.models import ProjectRiskLevel, RiskStatus
from risk_platform.risks.schemas import RiskDetail, RiskPage, RiskQuery
from risk_platform.risks.service import RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.todos.schemas import ManagerTodoDetail, ManagerTodoListResponse
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.schemas import WeeklyProjectDetail, WeeklyReportResponse
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
        None,  # type: ignore[arg-type]
        DashboardService(None),  # type: ignore[arg-type]
        RisksService(None),  # type: ignore[arg-type]
        TodosService(None),  # type: ignore[arg-type]
        WeeklyReportService(None),  # type: ignore[arg-type]
    )
    assert {item.name for item in registry.help(identity())} == {
        "project_search",
        "project_detail",
        "risk_category_list",
        "dashboard_summary",
        "dashboard_focus",
        "risk_list",
        "risk_detail",
        "todo_list",
        "todo_detail",
        "weekly_report",
        "weekly_report_detail",
    }
    assert "project_search" in {
        item["name"] for item in registry.catalogue(identity())
    }
    assert "project_search" not in {
        item["name"] for item in registry.catalogue(identity(), selected_project_id=uuid.uuid4())
    }
    assert registry.help(identity(permissions=["agent.use"])) == []

    with pytest.raises(ApiError) as error:
        asyncio.run(registry.invoke(identity(), "arbitrary_sql", {}, trace_id="trace"))
    assert error.value.code == "AGENT_TOOL_NOT_ALLOWED"


def test_tool_registry_fails_closed_when_a_tool_returns_none() -> None:
    registry = AgentToolRegistry(
        None,  # type: ignore[arg-type]
        DashboardService(None),  # type: ignore[arg-type]
        RisksService(None),  # type: ignore[arg-type]
        TodosService(None),  # type: ignore[arg-type]
        WeeklyReportService(None),  # type: ignore[arg-type]
    )
    original = registry._by_name["project_search"]

    async def returns_none(*_args: object, **_kwargs: object) -> None:
        return None

    registry._by_name["project_search"] = replace(original, call=returns_none)
    with pytest.raises(AgentToolResultTypeError):
        asyncio.run(registry.invoke(identity(), "project_search", {}, trace_id="trace"))


def test_risk_list_maps_bounded_level_and_status_to_risk_query() -> None:
    captured: list[object] = []

    class FakeRisks:
        async def list(self, _identity: SessionIdentity, query: object) -> RiskPage:
            captured.append(query)
            return RiskPage(items=[], page=1, pageSize=10, total=0)

        async def list_for_project(
            self, _identity: SessionIdentity, _project_id: UUID, query: object
        ) -> RiskPage:
            captured.append(query)
            return RiskPage(items=[], page=1, pageSize=10, total=0)

    call = AgentToolRegistry._risk_list(FakeRisks())  # type: ignore[arg-type]

    async def invoke() -> object:
        return await call(
            identity(),
            {"level": ProjectRiskLevel.HIGH, "status": RiskStatus.ACTIVE, "pageSize": 10},
        )

    result = asyncio.run(invoke())
    assert isinstance(result, AgentRiskListPage)
    query = captured[0]
    assert isinstance(query, RiskQuery)
    assert query.level is ProjectRiskLevel.HIGH
    assert query.status is RiskStatus.ACTIVE
    assert query.pageSize == 10


def test_all_agent_tools_adapt_their_declared_runtime_result_to_agent_tool_result() -> None:
    registry = AgentToolRegistry(
        None,  # type: ignore[arg-type]
        DashboardService(None),  # type: ignore[arg-type]
        RisksService(None),  # type: ignore[arg-type]
        TodosService(None),  # type: ignore[arg-type]
        WeeklyReportService(None),  # type: ignore[arg-type]
    )
    values: dict[str, object] = {
        "project_search": ProjectListToolResponse(items=[], page=1, pageSize=20, total=0),
        "project_detail": ProjectDetailToolResponse(
            id=uuid.uuid4(), name="项目", alias=None, status="DELIVERY"
        ),
        "risk_category_list": RiskCategoryListToolResponse(items=[]),
        "dashboard_summary": DashboardSummary.model_construct(),
        "dashboard_focus": [],
        "risk_list": AgentRiskListPage.model_construct(),
        "risk_detail": RiskDetail.model_construct(),
        "todo_list": ManagerTodoListResponse.model_construct(),
        "todo_detail": ManagerTodoDetail.model_construct(),
        "weekly_report": WeeklyReportResponse.model_construct(),
        "weekly_report_detail": WeeklyProjectDetail.model_construct(),
    }
    arguments = {
        "project_search": {},
        "project_detail": {"projectId": str(uuid.uuid4())},
        "risk_category_list": {},
        "dashboard_summary": {},
        "dashboard_focus": {},
        "risk_list": {},
        "risk_detail": {"riskId": str(uuid.uuid4())},
        "todo_list": {},
        "todo_detail": {"todoId": str(uuid.uuid4())},
        "weekly_report": {},
        "weekly_report_detail": {
            "weekStart": "2026-08-17T00:00:00+00:00",
            "projectId": str(uuid.uuid4()),
        },
    }

    def call_for(value: object) -> Callable[..., Awaitable[object]]:
        async def call(*_args: object, **_kwargs: object) -> object:
            return value

        return call

    for name, value in values.items():
        registry._by_name[name] = replace(registry._by_name[name], call=call_for(value))

    async def run() -> list[str]:
        return [
            (await registry.invoke(identity(), name, arguments[name], trace_id="trace")).tool
            for name in values
        ]

    assert asyncio.run(run()) == list(values)


def test_conversation_persistence_owner_scope_and_frozen_retention(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        assert created.userMessage.sequence == 1
        assert created.conversation.lastMessageSequence == 1
        assert created.conversation.expiresAt == created.conversation.createdAt + timedelta(days=90)

        # T029 permits the next user message only after the prior durable execution is terminal.
        async with transaction(database) as session:
            task = await session.scalar(
                select(DurableTask).where(
                    DurableTask.kind == DurableTaskKind.AGENT_EXECUTION,
                    DurableTask.payload["conversation_id"].as_string()
                    == str(created.conversation.id),
                )
            )
            assert task is not None
            task.status = DurableTaskStatus.SUCCEEDED
            task.completedAt = datetime.now(UTC)

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


def test_history_restores_latest_window_for_long_conversation(
    database: async_sessionmaker[AsyncSession],
) -> None:
    # A conversation with 150+ messages must restore the *latest* window, not
    # the oldest 100: after a refresh the user should see the most recent
    # USER/ASSISTANT turns and the next send continues the same conversation
    # (nextMessageSequence reflects the true tail).
    total = 150

    async def run() -> None:
        app_service = service(database)
        conversation_id = uuid.uuid4()
        async with transaction(database) as session:
            now = datetime.now(UTC)
            # lastMessageSequence starts at 0; the per-row trigger
            # agent_messages_assign_sequence advances it by one per inserted
            # message and requires each NEW.sequence to equal the running
            # value, so sequences must be inserted in ascending order.
            session.add(
                AgentConversation(
                    id=conversation_id,
                    ownerUserId=OWNER,
                    createdAt=now,
                    updatedAt=now,
                    expiresAt=now + timedelta(days=90),
                    retentionConfigVersion="test",
                )
            )
            await session.flush()
            session.add_all(
                AgentMessage(
                    conversationId=conversation_id,
                    sequence=sequence,
                    role=AgentMessageRole.USER if sequence % 2 == 1 else AgentMessageRole.ASSISTANT,
                    content=f"消息 {sequence}",
                    traceId="t028-trace",
                    dataAsOf=now,
                )
                for sequence in range(1, total + 1)
            )
            await session.flush()

        history = await app_service.history(identity(), conversation_id)
        # The latest 100 messages (sequences 51..150) are restored in ascending
        # order; nothing older than 51 leaks into the window.
        assert len(history.messages) == 100
        assert [message.sequence for message in history.messages] == list(range(51, 151))
        assert [message.content for message in history.messages][:2] == ["消息 51", "消息 52"]
        assert history.messages[-1].content == "消息 150"
        # The tail is the true last sequence + 1, so the next send continues.
        assert history.nextMessageSequence == total + 1

    asyncio.run(run())
