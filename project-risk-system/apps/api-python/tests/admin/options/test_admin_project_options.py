"""T044 admin project-options selector.

Exercises ``GET /api/admin/projects/options`` under ``admin.scope.manage``
against an isolated, Alembic-created PostgreSQL 16 schema (skipped without
``TEST_DATABASE_URL``), asserting the legacy contract, scope, ordering and
envelope/error semantics.
"""

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
from risk_platform.admin.options.api import get_admin_options_service, router
from risk_platform.admin.options.service import AdminOptionsService
from risk_platform.app import AppComposition, create_app
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.seed import SeedSettings, seed_reference_data

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def options_database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL T044 validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t044_{uuid.uuid4().hex}"
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

    async def seed() -> None:
        async with transaction(factory) as session:
            await seed_reference_data(
                session,
                SeedSettings(
                    username="admin",
                    display_name="管理员",
                    password="Seed_Admin9!Pass",
                    password_min_length=12,
                ),
            )

    try:
        asyncio.run(seed())
        yield factory
    finally:
        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


async def _identity(
    factory: async_sessionmaker[AsyncSession], permissions: list[str]
) -> SessionIdentity:
    async with factory() as session:
        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(admin.id),
            username=admin.username,
            displayName=admin.displayName,
            departmentName="技术管理部",
            roleCodes=["SYSTEM_ADMIN"],
            permissions=permissions,
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


async def _seed_projects(factory: async_sessionmaker[AsyncSession]) -> None:
    """Create projects spanning every selector branch the legacy contract covers."""

    async with factory() as session:
        tech = await session.scalar(select(Department).where(Department.code == "TECH_MANAGEMENT"))
        risk = await session.scalar(select(Department).where(Department.code == "RISK_MANAGEMENT"))
        assert tech is not None and risk is not None
        tech_id = tech.id
        risk_id = risk.id
    async with transaction(factory) as session:
        # Zebra sorts before Alpha lexically only if ordering were absent; the
        # selector must reorder to Alpha, Loose, Zebra by name ascending.
        session.add(
            Project(
                name="Zebra 项目",
                departmentId=tech_id,
                externalCode="EXT-001",
                status=ProjectStatus.DELIVERY,
            )
        )
        session.add(
            Project(
                name="Alpha 项目",
                departmentId=risk_id,
                externalCode=None,
                status=ProjectStatus.COMPLETED,
            )
        )
        session.add(
            Project(
                name="Loose 项目",
                departmentId=None,
                externalCode="EXT-002",
                status=ProjectStatus.DELIVERY,
            )
        )
        # Archived projects must never appear in the selector.
        session.add(
            Project(
                name="Archived 项目",
                departmentId=tech_id,
                externalCode="EXT-OLD",
                status=ProjectStatus.ARCHIVED,
            )
        )


def _client(
    factory: async_sessionmaker[AsyncSession], identity: SessionIdentity
) -> httpx2.AsyncClient:
    options_service = AdminOptionsService(factory)

    async def override_identity() -> SessionIdentity:
        return identity

    app = create_app(
        Settings(environment="test", cors_origins=("https://web.internal",)),
        AppComposition(
            routers=(router,),
            dependency_overrides={
                current_identity: override_identity,
                get_admin_options_service: lambda: options_service,
            },
        ),
    )
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
    )


def test_projects_options_excludes_archived_orders_by_name_and_joins_department(
    options_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        await _seed_projects(options_database)
        identity = await _identity(options_database, ["admin.scope.manage"])
        client = _client(options_database, identity)
        async with client:
            response = await client.get("/api/admin/projects/options")
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "OK"
        data = body["data"]
        names = [item["name"] for item in data]
        # Archived project excluded; remaining three ordered by name ascending.
        assert "Archived 项目" not in names
        assert names == ["Alpha 项目", "Loose 项目", "Zebra 项目"]
        by_name = {item["name"]: item for item in data}
        # Exact contract keys only — no leakage of internal columns.
        assert set(by_name["Alpha 项目"]) == {"id", "externalCode", "name", "departmentName"}
        # externalCode nullable is preserved.
        assert by_name["Alpha 项目"]["externalCode"] is None
        # departmentName comes from the joined department.
        assert by_name["Alpha 项目"]["departmentName"] == "风险管理组"
        assert by_name["Zebra 项目"]["departmentName"] == "技术管理部"
        # Projects without a department surface a null departmentName.
        assert by_name["Loose 项目"]["departmentName"] is None

    asyncio.run(scenario())


def test_projects_options_requires_admin_scope_manage(
    options_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        await _seed_projects(options_database)
        # A user holding only user-management cannot reach the scope selector.
        identity = await _identity(options_database, ["admin.user.manage"])
        client = _client(options_database, identity)
        async with client:
            response = await client.get("/api/admin/projects/options")
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    asyncio.run(scenario())
