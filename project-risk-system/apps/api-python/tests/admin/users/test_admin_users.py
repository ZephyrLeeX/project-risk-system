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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.admin.models import User, UserStatus
from risk_platform.admin.users.api import get_admin_users_service, router
from risk_platform.admin.users.service import AdminUsersService
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.models import AuditLog, AuditResult
from risk_platform.auth.api import current_identity
from risk_platform.auth.models import Session
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.projects.models import Project
from risk_platform.rbac.models import Role
from risk_platform.seed import SeedSettings, seed_reference_data

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def users_database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL admin users validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t011_{uuid.uuid4().hex}"
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
    engine: AsyncEngine = create_database_engine(f"{sync_url}?options=-csearch_path%3D{schema}")
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
        await seed_reference_data(
            session,
            SeedSettings(
                username="admin",
                display_name="管理员",
                password="Seed_Admin9!Pass",
                password_min_length=12,
            ),
        )


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


async def _project(factory: async_sessionmaker[AsyncSession]) -> Project:
    async with transaction(factory) as session:
        project = Project(name=f"T011 project {uuid.uuid4().hex}")
        session.add(project)
        await session.flush()
        return project


async def _client(
    factory: async_sessionmaker[AsyncSession], identity: SessionIdentity
) -> httpx2.AsyncClient:
    service = AdminUsersService(factory)

    async def override_identity() -> SessionIdentity:
        return identity

    app = create_app(
        Settings(environment="test", cors_origins=("https://web.internal",)),
        AppComposition(
            routers=(router,),
            dependency_overrides={
                current_identity: override_identity,
                get_admin_users_service: lambda: service,
            },
        ),
    )
    transport = httpx2.ASGITransport(app=app)
    return httpx2.AsyncClient(transport=transport, base_url="https://testserver")


def test_user_lifecycle_revokes_sessions_assigns_projects_and_audits(
    users_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(users_database, ["admin.user.manage", "admin.scope.manage"])
        project = await _project(users_database)
        client = await _client(users_database, identity)
        try:
            async with users_database() as session:
                manager = await session.scalar(select(Role).where(Role.code == "PROJECT_MANAGER"))
                department_id = await session.scalar(
                    select(User.departmentId).where(User.id == uuid.UUID(identity.user.id))
                )
                assert manager is not None and department_id is not None
            payload = {
                "displayName": "测试项目经理",
                "username": f"manager-{uuid.uuid4().hex[:8]}",
                "email": "manager@example.invalid",
                "departmentId": str(department_id),
                "roleId": str(manager.id),
                "dataScope": "ASSIGNED",
                "projectIds": [str(project.id)],
                "enabled": True,
            }
            created = await client.post(
                "/api/admin/users", headers={"origin": "https://web.internal"}, json=payload
            )
            assert created.status_code == 200
            body = created.json()["data"]
            assert body["initialPassword"].startswith("Risk!")
            user_id = uuid.UUID(body["user"]["id"])
            assert body["user"]["assignedProjectIds"] == [str(project.id)]
            assert body["user"]["mustChangePassword"] is True

            async with transaction(users_database) as session:
                session.add(
                    Session(
                        tokenHash=uuid.uuid4().hex,
                        userId=user_id,
                        expiresAt=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
            disabled = await client.post(
                f"/api/admin/users/{user_id}/status",
                headers={"origin": "https://web.internal"},
                json={"status": "DISABLED"},
            )
            assert disabled.status_code == 200
            async with users_database() as session:
                user = await session.get(User, user_id)
                active_session = await session.scalar(
                    select(Session).where(Session.userId == user_id)
                )
                assert user is not None and user.status is UserStatus.DISABLED
                assert active_session is not None and active_session.revokedAt is not None

            reset = await client.post(
                f"/api/admin/users/{user_id}/reset-password",
                headers={"origin": "https://web.internal"},
            )
            assert reset.status_code == 200
            assert reset.json()["data"]["initialPassword"].startswith("Risk!")
            records = await client.get(f"/api/admin/users/{user_id}/records")
            assert records.status_code == 200
            assert {record["action"] for record in records.json()["data"]} >= {
                "ADMIN_USER_CREATED",
                "ADMIN_USER_DISABLED",
                "ADMIN_USER_PASSWORD_RESET",
            }
            async with users_database() as session:
                events = (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.resourceId == str(user_id))
                    )
                ).all()
                assert len(events) >= 3
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_user_api_rejects_unauthorized_invalid_assignment_and_self_disable(
    users_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        denied_client = await _client(users_database, await _identity(users_database, []))
        try:
            assert (await denied_client.get("/api/admin/users")).status_code == 403
        finally:
            await denied_client.aclose()
        identity = await _identity(users_database, ["admin.user.manage", "admin.scope.manage"])
        client = await _client(users_database, identity)
        try:
            own_id = identity.user.id
            assert (
                await client.post(
                    f"/api/admin/users/{own_id}/status",
                    headers={"origin": "https://web.internal"},
                    json={"status": "DISABLED"},
                )
            ).status_code == 403
            scopes = await client.put(
                f"/api/admin/users/{own_id}/project-scopes",
                headers={"origin": "https://web.internal"},
                json={"dataScope": "ALL", "projectIds": [str(uuid.uuid4())]},
            )
            assert scopes.status_code == 400
            assert scopes.json()["message"] == "当前数据范围不使用指定项目授权"
            async with users_database() as session:
                failure_actions = set(
                    await session.scalars(
                        select(AuditLog.action).where(
                            AuditLog.resourceId == own_id,
                            AuditLog.result == AuditResult.FAILURE,
                        )
                    )
                )
                assert {"ADMIN_USER_STATUS_UPDATED", "ADMIN_USER_SCOPE_UPDATED"} <= failure_actions

                manager = await session.scalar(select(Role).where(Role.code == "PROJECT_MANAGER"))
                department_id = await session.scalar(
                    select(User.departmentId).where(User.id == uuid.UUID(own_id))
                )
                assert manager is not None and department_id is not None
            username = f"conflict-{uuid.uuid4().hex[:8]}"
            payload = {
                "displayName": "冲突测试用户",
                "username": username,
                "departmentId": str(department_id),
                "roleId": str(manager.id),
                "dataScope": "OWNED",
                "projectIds": [],
                "enabled": True,
            }
            assert (
                await client.post(
                    "/api/admin/users", headers={"origin": "https://web.internal"}, json=payload
                )
            ).status_code == 200
            duplicate = await client.post(
                "/api/admin/users", headers={"origin": "https://web.internal"}, json=payload
            )
            assert duplicate.status_code == 409
            async with users_database() as session:
                count = await session.scalar(
                    select(func.count()).select_from(User).where(User.username == username)
                )
                assert count == 1
                assert (
                    await session.scalar(
                        select(AuditLog.id).where(
                            AuditLog.action == "ADMIN_USER_CREATED",
                            AuditLog.result == AuditResult.FAILURE,
                            AuditLog.resourceId.is_(None),
                        )
                    )
                    is not None
                )
        finally:
            await client.aclose()

    asyncio.run(scenario())
