from __future__ import annotations

import os
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.rbac.guards import has_all_permissions, require_permissions
from risk_platform.rbac.models import DataScopeType, UserProjectScope
from risk_platform.rbac.scopes import apply_project_scope, get_scoped_project
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def test_permission_guard_requires_all_permissions() -> None:
    assert has_all_permissions(["risk.report", "risk.resolve"], ["risk.report"])
    assert not has_all_permissions(["risk.report"], ["risk.report", "risk.resolve"])


def test_project_scope_predicates_are_postgresql_and_exclude_archived() -> None:
    for scope in DataScopeType:
        statement = apply_project_scope(select(Project), USER_ID, scope)
        compiled = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
        assert "projects.status" in compiled or '"projects"."status"' in compiled
    combined = str(
        apply_project_scope(select(Project), USER_ID, DataScopeType.OWNED_OR_ASSIGNED).compile(
            dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
        )
    )
    assert 'projects."managerId"' in combined or '"projects"."managerId"' in combined
    assert "user_project_scopes" in combined


def test_permission_dependency_preserves_authenticated_identity() -> None:
    identity = SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC),
        user=AuthenticatedUser(
            id=str(USER_ID), username="u", displayName="U", departmentName=None,
            roleCodes=["PROJECT_MANAGER"], permissions=["risk.report"],
            dataScope="OWNED", mustChangePassword=False,
        ),
    )
    dependency = cast(
        Callable[[SessionIdentity], Awaitable[SessionIdentity]],
        require_permissions("risk.report"),
    )
    import asyncio

    async def allowed() -> SessionIdentity:
        return await dependency(identity)

    assert asyncio.run(allowed()) is identity
    denied = cast(
        Callable[[SessionIdentity], Awaitable[SessionIdentity]],
        require_permissions("risk.resolve"),
    )
    async def forbidden() -> SessionIdentity:
        return await denied(identity)

    with pytest.raises(ApiError, match="FORBIDDEN"):
        asyncio.run(forbidden())
    assert current_identity is not None


@pytest.fixture
def postgres_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL RBAC validation 未执行")
    return re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)


@pytest.fixture
def rbac_database(postgres_url: str) -> Iterator[async_sessionmaker[AsyncSession]]:
    schema = f"t010_{uuid.uuid4().hex}"
    admin_engine = create_engine(postgres_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(
        postgres_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()
    from risk_platform.db import create_database_engine, create_session_factory

    engine = create_database_engine(f"{postgres_url}?options=-csearch_path%3D{schema}")
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        import asyncio

        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_scope_matrix_returns_only_authorized_active_projects(
    rbac_database: async_sessionmaker[AsyncSession],
) -> None:
    import asyncio

    async def run() -> None:
        async with rbac_database() as session:
            session.add(
                User(
                    id=USER_ID,
                    username="t010-user",
                    passwordHash="not-a-real-password-hash",
                    displayName="T010",
                )
            )
            await session.flush()
            owned = Project(name="owned", status=ProjectStatus.DELIVERY, managerId=USER_ID)
            assigned = Project(name="assigned", status=ProjectStatus.DELIVERY)
            archived = Project(name="archived", status=ProjectStatus.ARCHIVED, managerId=USER_ID)
            session.add_all([owned, assigned, archived])
            await session.flush()
            session.add(UserProjectScope(projectId=assigned.id, userId=USER_ID))
            await session.commit()
            for scope, expected in (
                (DataScopeType.ALL, {owned.id, assigned.id}),
                (DataScopeType.OWNED, {owned.id}),
                (DataScopeType.ASSIGNED, {assigned.id}),
                (DataScopeType.OWNED_OR_ASSIGNED, {owned.id, assigned.id}),
                (DataScopeType.NONE, set()),
            ):
                rows = (await session.scalars(
                    apply_project_scope(select(Project), USER_ID, scope)
                )).all()
                assert {row.id for row in rows} == expected
            assert (
                await get_scoped_project(session, archived.id, USER_ID, DataScopeType.ALL)
                is None
            )

    asyncio.run(run())
