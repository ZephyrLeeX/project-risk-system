"""T047 — `/api/todos` SQL pagination remediation.

Proves the manager todo list endpoint paginates at the database query layer
(``LIMIT``/``OFFSET`` + an independent total count) instead of materializing the
full scoped result set, while preserving permissions, DataScope, owner/status
filters, archived exclusion, stable ordering and the full-scoped summary/owners
semantics the frontend depends on.

Runs against real PostgreSQL 16 with an isolated per-run schema at Alembic head.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.rbac.models import DataScopeType, UserProjectScope
from risk_platform.todos.models import (
    ActionItem,
    ActionItemSourceType,
    ActionItemStatus,
    ActionItemUrgency,
)
from risk_platform.todos.schemas import ListTodosQuery, ManagerTodoListResponse
from risk_platform.todos.service import TodosService

ROOT = Path(__file__).resolve().parents[2]
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000047")
TodoDb = tuple[async_sessionmaker[AsyncSession], AsyncEngine]

# Controlled scoped set (all in P_OWNED, visible to OWNED scope): 25 todos with
# 10 PENDING / 8 IN_PROGRESS / 7 COMPLETED and a mix of urgencies/owners.
_CONTROLLED: list[tuple[ActionItemStatus, ActionItemUrgency, str | None, date | None]] = [
    *[
        (ActionItemStatus.PENDING, ActionItemUrgency.EMERGENCY, "张三", date(2026, 8, 18))
        for _ in range(3)
    ],
    *[
        (ActionItemStatus.PENDING, ActionItemUrgency.HIGH, "李四", date(2026, 8, 19))
        for _ in range(4)
    ],
    *[
        (ActionItemStatus.PENDING, ActionItemUrgency.NORMAL, None, None)
        for _ in range(3)
    ],
    *[
        (ActionItemStatus.IN_PROGRESS, ActionItemUrgency.HIGH, "张三", date(2026, 8, 20))
        for _ in range(5)
    ],
    *[
        (ActionItemStatus.IN_PROGRESS, ActionItemUrgency.NORMAL, "李四", date(2026, 8, 21))
        for _ in range(3)
    ],
    *[
        (ActionItemStatus.COMPLETED, ActionItemUrgency.NORMAL, "张三", date(2026, 8, 10))
        for _ in range(4)
    ],
    *[
        (ActionItemStatus.COMPLETED, ActionItemUrgency.NORMAL, None, date(2026, 8, 11))
        for _ in range(3)
    ],
]
LARGE_COUNT = 500


def _url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL pagination validation 未执行")
    return re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)


def _build_factory(url: str, schema: str) -> TodoDb:
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()
    engine = create_database_engine(f"{url}?options=-csearch_path%3D{schema}")
    factory = create_session_factory(engine)
    return factory, engine


def _dispose(url: str, schema: str, engine: AsyncEngine) -> None:
    asyncio.run(engine.dispose())
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin_engine.dispose()


@pytest.fixture(scope="module")
def todos_database() -> Iterator[TodoDb]:
    url = _url()
    schema = f"t047_{uuid.uuid4().hex}"
    factory, engine = _build_factory(url, schema)
    try:
        asyncio.run(_seed_controlled(factory))
        yield factory, engine
    finally:
        _dispose(url, schema, engine)


@pytest.fixture(scope="module")
def large_todos_database() -> Iterator[TodoDb]:
    url = _url()
    schema = f"t047large_{uuid.uuid4().hex}"
    factory, engine = _build_factory(url, schema)
    try:
        asyncio.run(_seed_large(factory))
        yield factory, engine
    finally:
        _dispose(url, schema, engine)


async def _seed_controlled(factory: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(factory) as session:
        session.add(
            User(
                id=USER_ID,
                username="t047-user",
                passwordHash="not-a-real-password-hash",
                displayName="T047",
            )
        )
        await session.flush()
        owned = Project(
            name="本人负责项目",
            managerId=USER_ID,
            deliveryOwnerName="本人",
            annualPlanAmount=120,
            remainingAmount=100,
            actualCollectedAmount=20,
        )
        # ``assigned``/``other`` are not managed by USER_ID; their managerId is
        # left null so the projects_managerId_fkey constraint holds without
        # inventing phantom users. ``assigned`` is still visible via ASSIGNED
        # scope through UserProjectScope; ``other`` is fully out of scope.
        assigned = Project(
            name="指定项目",
            managerId=None,
            deliveryOwnerName="指定",
            annualPlanAmount=240,
            remainingAmount=200,
            actualCollectedAmount=40,
        )
        other = Project(
            name="范围外项目",
            managerId=None,
            deliveryOwnerName="范围外",
            annualPlanAmount=360,
            remainingAmount=300,
            actualCollectedAmount=60,
        )
        archived = Project(
            name="归档项目",
            managerId=USER_ID,
            deliveryOwnerName="归档",
            annualPlanAmount=480,
            remainingAmount=400,
            actualCollectedAmount=80,
            status=ProjectStatus.ARCHIVED,
        )
        session.add_all([owned, assigned, other, archived])
        await session.flush()
        session.add(UserProjectScope(projectId=assigned.id, userId=USER_ID))

        for status, urgency, assignee, due in _CONTROLLED:
            session.add(
                ActionItem(
                    projectId=owned.id,
                    title=f"本人待办 {status.value} {urgency.value}",
                    description="T047 controlled todo",
                    urgency=urgency,
                    status=status,
                    sourceType=ActionItemSourceType.MANUAL,
                    assigneeNameSource=assignee,
                    dueDate=due,
                )
            )
        # Out-of-OWNED-scope and archived todos that must never appear.
        for project, label in ((assigned, "指定"), (other, "范围外"), (archived, "归档")):
            session.add(
                ActionItem(
                    projectId=project.id,
                    title=f"{label}待办",
                    description="T047 scope probe",
                    urgency=ActionItemUrgency.NORMAL,
                    status=ActionItemStatus.PENDING,
                    sourceType=ActionItemSourceType.MANUAL,
                    assigneeNameSource="探针",
                )
            )


async def _seed_large(factory: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(factory) as session:
        session.add(
            User(
                id=USER_ID,
                username="t047-large-user",
                passwordHash="not-a-real-password-hash",
                displayName="T047-Large",
            )
        )
        await session.flush()
        project = Project(
            name="大批量项目",
            managerId=USER_ID,
            deliveryOwnerName="本人",
            annualPlanAmount=120,
            remainingAmount=100,
            actualCollectedAmount=20,
        )
        session.add(project)
        await session.flush()
        base = datetime(2026, 8, 1, tzinfo=UTC)
        for index in range(LARGE_COUNT):
            session.add(
                ActionItem(
                    projectId=project.id,
                    title=f"大批量待办 {index}",
                    description="T047 large fixture todo",
                    urgency=ActionItemUrgency.NORMAL,
                    status=ActionItemStatus.PENDING,
                    sourceType=ActionItemSourceType.MANUAL,
                    assigneeNameSource="张三",
                    dueDate=date(2026, 8, 1) + timedelta(days=index % 28),
                    updatedAt=base + timedelta(seconds=index),
                )
            )


def _identity(scope: DataScopeType) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(USER_ID),
            username="t047",
            displayName="T047",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=["dashboard.view"],
            dataScope=scope.value,
            mustChangePassword=False,
        ),
    )


def _list(
    factory: async_sessionmaker[AsyncSession], scope: DataScopeType, **query: object
) -> ManagerTodoListResponse:
    async def scenario() -> ManagerTodoListResponse:
        service = TodosService(factory)
        return await service.list(_identity(scope), ListTodosQuery.model_validate(query))

    return asyncio.run(scenario())


def test_first_middle_last_pages_cover_all_rows_without_duplicates(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    seen: list[uuid.UUID] = []
    for page in (1, 2, 3):
        result = _list(factory, DataScopeType.OWNED, page=page, pageSize=10)
        assert result.page == page
        assert result.pageSize == 10
        assert result.total == 25
        page_ids = [item.id for item in result.items]
        assert len(page_ids) == (10 if page < 3 else 5)
        seen.extend(page_ids)
    assert len(seen) == 25
    assert len(set(seen)) == 25  # no duplicates, no missing rows across pages


def test_default_page_and_page_size(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    result = _list(factory, DataScopeType.OWNED)
    assert result.page == 1
    assert result.pageSize == 20
    assert result.total == 25
    assert len(result.items) == 20


def test_empty_page_beyond_last(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    result = _list(factory, DataScopeType.OWNED, page=4, pageSize=10)
    assert result.page == 4
    assert result.pageSize == 10
    assert result.total == 25
    assert result.items == []


def test_status_filter_paginates_the_filtered_set(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    result = _list(factory, DataScopeType.OWNED, status="IN_PROGRESS", page=1, pageSize=10)
    assert result.total == 8
    assert len(result.items) == 8
    assert {item.status for item in result.items} == {"IN_PROGRESS"}


def test_owner_filter_paginates_the_filtered_set(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    # "张三" owns 3 PENDING + 5 IN_PROGRESS + 4 COMPLETED = 12 of the controlled set.
    result = _list(factory, DataScopeType.OWNED, owner="张三", page=1, pageSize=20)
    assert result.total == 12
    assert {item.assigneeName for item in result.items} == {"张三"}


def test_summary_and_owners_are_full_scoped_under_filter(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    filtered = _list(factory, DataScopeType.OWNED, status="PENDING", page=1, pageSize=5)
    # total is the filtered count, but summary/owners reflect the full scoped set.
    assert filtered.total == 10
    assert len(filtered.items) == 5
    assert filtered.summary.total == 25
    assert filtered.summary.pending == 10
    assert filtered.summary.inProgress == 8
    assert filtered.summary.completed == 7
    # emergency = EMERGENCY & not COMPLETED = 3 PENDING EMERGENCY.
    assert filtered.summary.emergency == 3
    # owners span the full scoped set, including assignees with no PENDING todo.
    assert set(filtered.owners) == {"张三", "李四", "待分配"}


def test_data_scope_excludes_out_of_scope_and_archived(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    all_scope = _list(factory, DataScopeType.ALL, page=1, pageSize=50)
    # ALL scope sees the 3 active projects' todos but not the archived one.
    assert all_scope.total == 27  # 25 owned + 1 assigned + 1 other
    titles = {item.title for item in all_scope.items}
    assert "归档待办" not in titles
    assert "指定待办" in titles and "范围外待办" in titles

    owned_scope = _list(factory, DataScopeType.OWNED, page=1, pageSize=50)
    assert owned_scope.total == 25
    owned_titles = {item.title for item in owned_scope.items}
    assert "指定待办" not in owned_titles
    assert "范围外待办" not in owned_titles


def test_ordering_is_stable_and_deterministic(todos_database: TodoDb) -> None:
    factory, _engine = todos_database
    first = _list(factory, DataScopeType.OWNED, page=1, pageSize=10)
    repeat = _list(factory, DataScopeType.OWNED, page=1, pageSize=10)
    assert [item.id for item in first.items] == [item.id for item in repeat.items]
    # First page leads with non-completed items (PENDING < IN_PROGRESS < COMPLETED).
    assert all(item.status != "COMPLETED" for item in first.items[:8])


def test_large_fixture_paginates_at_sql_layer_with_limit_and_offset(
    large_todos_database: TodoDb,
) -> None:
    factory, engine = large_todos_database
    statements: list[str] = []

    def before_cursor_execute(
        _conn: object, _cursor: object, statement: str, *_args: object
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    try:
        result = _list(factory, DataScopeType.OWNED, page=3, pageSize=20)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", before_cursor_execute)

    assert result.total == LARGE_COUNT
    assert len(result.items) == 20
    # The items query is paginated in SQL: exactly one statement carries both
    # LIMIT and OFFSET. The only other LIMIT-bearing select is the bounded
    # schedule query (LIMIT, no OFFSET); every remaining select is an aggregate
    # or distinct query, so no statement materializes the full 500-row set.
    paginated = [s for s in statements if "LIMIT" in s and "OFFSET" in s]
    assert len(paginated) == 1, statements
    # The total is produced by a separate aggregate count, not by materializing
    # the full set and slicing in Python.
    assert any("count(" in s.lower() and "LIMIT" not in s for s in statements), statements


def test_pagination_performance_smoke_is_bounded(
    large_todos_database: TodoDb,
) -> None:
    """Scoped smoke proving pagination bounds the response; not an ADR 0032 gate."""
    factory, _engine = large_todos_database
    start = time.perf_counter()
    result = _list(factory, DataScopeType.OWNED, page=1, pageSize=20)
    elapsed = time.perf_counter() - start
    assert result.total == LARGE_COUNT
    assert len(result.items) == 20
    # Bounded pagination must stay well under the unbounded ~2s/single-request
    # behaviour T038 measured; this is a smoke bound, not a capacity gate.
    assert elapsed < 2.0, elapsed
