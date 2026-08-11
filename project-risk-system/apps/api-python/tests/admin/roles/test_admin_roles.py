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

from risk_platform.admin.models import User
from risk_platform.admin.options.api import get_admin_options_service
from risk_platform.admin.options.api import router as options_router
from risk_platform.admin.options.service import AdminOptionsService
from risk_platform.admin.roles.api import get_admin_roles_service, router
from risk_platform.admin.roles.policy import validate_role_policy
from risk_platform.admin.roles.schemas import CreateRoleRequest
from risk_platform.admin.roles.service import AdminRolesService
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.models import AuditLog
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.rbac.models import DataScopeType
from risk_platform.seed import SeedSettings, seed_reference_data
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[3]


def test_role_policy_keeps_system_and_mailbox_boundaries() -> None:
    with pytest.raises(ApiError) as system_error:
        validate_role_policy("SYSTEM_ADMIN", ["dashboard.view"], DataScopeType.ALL)
    assert "核心权限" in system_error.value.message
    with pytest.raises(ApiError) as mailbox_error:
        validate_role_policy("PROJECT_MANAGER", ["mailbox.sync_self"], DataScopeType.OWNED)
    assert "邮箱" in mailbox_error.value.message
    validate_role_policy(
        "PROJECT_MANAGER",
        ["dashboard.view", "agent.use", "risk.report", "risk.resolve"],
        DataScopeType.OWNED_OR_ASSIGNED,
    )


@pytest.fixture
def roles_database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL role administration validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t012_{uuid.uuid4().hex}"
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


async def _client(
    factory: async_sessionmaker[AsyncSession], identity: SessionIdentity
) -> httpx2.AsyncClient:
    service = AdminRolesService(factory)
    options_service = AdminOptionsService(factory)

    async def override_identity() -> SessionIdentity:
        return identity

    app = create_app(
        Settings(environment="test", cors_origins=("https://web.internal",)),
        AppComposition(
            routers=(router, options_router),
            dependency_overrides={
                current_identity: override_identity,
                get_admin_roles_service: lambda: service,
                get_admin_options_service: lambda: options_service,
            },
        ),
    )
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
    )


def test_role_api_mutations_and_negative_audits(
    roles_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(roles_database, ["admin.role.manage", "admin.user.manage"])
        client = await _client(roles_database, identity)
        try:
            permissions = await client.get("/api/admin/permissions")
            assert permissions.status_code == 200
            departments = await client.get("/api/admin/departments")
            assert departments.status_code == 200
            assert departments.json()["data"][0].keys() == {"id", "code", "name"}
            payload = {
                "name": "临时角色",
                "code": "TEMP_ROLE",
                "description": "测试角色",
                "enabled": True,
                "defaultDataScope": "OWNED",
                "permissionCodes": ["dashboard.view"],
            }
            created = await client.post(
                "/api/admin/roles",
                headers={"origin": "https://web.internal"},
                json=payload,
            )
            assert created.status_code == 200
            role_id = uuid.UUID(created.json()["data"]["id"])
            update_payload = {
                key: value for key, value in payload.items() if key != "code"
            }
            updated = await client.patch(
                f"/api/admin/roles/{role_id}",
                headers={"origin": "https://web.internal"},
                json={**update_payload, "name": "临时角色更新", "enabled": False},
            )
            assert updated.status_code == 200
            deleted = await client.delete(
                f"/api/admin/roles/{role_id}",
                headers={"origin": "https://web.internal"},
            )
            assert deleted.status_code == 200

            roles = await client.get("/api/admin/roles")
            system_role = next(
                item for item in roles.json()["data"] if item["code"] == "SYSTEM_ADMIN"
            )
            rejected = await client.patch(
                f"/api/admin/roles/{system_role['id']}",
                headers={"origin": "https://web.internal"},
                json={
                    "name": system_role["name"],
                    "description": system_role["description"],
                    "enabled": False,
                    "defaultDataScope": "ALL",
                    "permissionCodes": system_role["permissionCodes"],
                },
            )
            assert rejected.status_code == 400
            async with roles_database() as session:
                actions = (
                    await session.scalars(
                        select(AuditLog.action).where(AuditLog.resourceType == "ROLE")
                    )
                ).all()
                assert "ADMIN_ROLE_CREATED" in actions
                assert "ADMIN_ROLE_UPDATED" in actions
                assert "ADMIN_ROLE_DELETED" in actions
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_roles_require_permission(
    roles_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        client = await _client(roles_database, await _identity(roles_database, []))
        try:
            assert (await client.get("/api/admin/roles")).status_code == 403
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_role_request_rejects_duplicate_permissions() -> None:
    with pytest.raises(ValueError):
        CreateRoleRequest(
            name="测试角色",
            code="TEST_ROLE",
            enabled=True,
            defaultDataScope=DataScopeType.OWNED,
            permissionCodes=["dashboard.view", "dashboard.view"],
        )
