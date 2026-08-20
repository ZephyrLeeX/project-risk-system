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
from risk_platform.projects.models import Project, ProjectStatus
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
                "ownedProjectIds": [],
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
                "ownedProjectIds": [],
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


async def _owned_project(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner_name: str,
    status: ProjectStatus = ProjectStatus.DELIVERY,
) -> Project:
    async with transaction(factory) as session:
        project = Project(
            name=f"T011 owned {uuid.uuid4().hex}",
            deliveryOwnerName=owner_name,
            status=status,
        )
        session.add(project)
        await session.flush()
        return project


async def _manager_payload(
    factory: async_sessionmaker[AsyncSession],
    *,
    display_name: str,
    data_scope: str,
    owned_ids: list[uuid.UUID],
    project_ids: list[uuid.UUID] | None = None,
) -> dict[str, object]:
    async with factory() as session:
        manager = await session.scalar(select(Role).where(Role.code == "PROJECT_MANAGER"))
        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert manager is not None and admin is not None
        department_id = admin.departmentId
        assert department_id is not None
    return {
        "displayName": display_name,
        "username": f"manager-{uuid.uuid4().hex[:8]}",
        "email": None,
        "mobile": None,
        "departmentId": str(department_id),
        "roleId": str(manager.id),
        "dataScope": data_scope,
        "projectIds": [str(pid) for pid in (project_ids or [])],
        "ownedProjectIds": [str(pid) for pid in owned_ids],
        "enabled": True,
    }


async def _bound_owned_ids(
    factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> set[uuid.UUID]:
    async with factory() as session:
        rows = (
            await session.scalars(
                select(Project.id).where(Project.managerId == user_id)
            )
        ).all()
        return set(rows)


def test_owned_projects_bind_partial_unbind_and_rename_preserves(
    users_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(users_database, ["admin.user.manage", "admin.scope.manage"])
        client = await _client(users_database, identity)
        try:
            owned = [
                await _owned_project(users_database, owner_name="李四") for _ in range(3)
            ]
            owned_ids = [project.id for project in owned]
            created = await client.post(
                "/api/admin/users",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四",
                    data_scope="OWNED",
                    owned_ids=owned_ids,
                ),
            )
            assert created.status_code == 200
            user_id = uuid.UUID(created.json()["data"]["user"]["id"])
            assert created.json()["data"]["user"]["ownedProjectCount"] == 3
            assert set(created.json()["data"]["user"]["ownedProjectIds"]) == {
                str(pid) for pid in owned_ids
            }
            assert await _bound_owned_ids(users_database, user_id) == set(owned_ids)

            # Only two of the three stay selected on update; the third unbinds.
            updated = await client.patch(
                f"/api/admin/users/{user_id}",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四",
                    data_scope="OWNED",
                    owned_ids=owned_ids[:2],
                ),
            )
            assert updated.status_code == 200
            assert set(updated.json()["data"]["user"]["ownedProjectIds"]) == {
                str(pid) for pid in owned_ids[:2]
            }
            assert await _bound_owned_ids(users_database, user_id) == set(owned_ids[:2])
            async with transaction(users_database) as session:
                unbound = await session.get(Project, owned_ids[2])
                assert unbound is not None and unbound.managerId is None

            # Renaming the user only re-derives recommendations; the bound
            # managerId and ownedProjectIds are preserved.
            renamed = await client.patch(
                f"/api/admin/users/{user_id}",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四改名",
                    data_scope="OWNED",
                    owned_ids=owned_ids[:2],
                ),
            )
            assert renamed.status_code == 200
            assert set(renamed.json()["data"]["user"]["ownedProjectIds"]) == {
                str(pid) for pid in owned_ids[:2]
            }
            async with transaction(users_database) as session:
                kept = await session.get(Project, owned_ids[0])
                assert kept is not None and kept.managerId == user_id
                user = await session.get(User, user_id)
                assert user is not None and user.displayName == "李四改名"
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_owned_projects_conflict_and_archived_are_rejected(
    users_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(users_database, ["admin.user.manage", "admin.scope.manage"])
        client = await _client(users_database, identity)
        try:
            # 王五 already manages the project; 李四 must not silently take it over.
            contested = await _owned_project(users_database, owner_name="李四")
            wangwu = await client.post(
                "/api/admin/users",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="王五",
                    data_scope="OWNED",
                    owned_ids=[contested.id],
                ),
            )
            assert wangwu.status_code == 200
            wangwu_id = uuid.UUID(wangwu.json()["data"]["user"]["id"])

            conflicting = await client.post(
                "/api/admin/users",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四",
                    data_scope="OWNED",
                    owned_ids=[contested.id],
                ),
            )
            assert conflicting.status_code == 409
            assert conflicting.json()["code"] == "PROJECT_MANAGER_CONFLICT"
            async with transaction(users_database) as session:
                project = await session.get(Project, contested.id)
                assert project is not None and project.managerId == wangwu_id

            # Archived projects can never be bound to a manager account.
            archived = await _owned_project(
                users_database, owner_name="李四", status=ProjectStatus.ARCHIVED
            )
            archived_response = await client.post(
                "/api/admin/users",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四",
                    data_scope="OWNED",
                    owned_ids=[archived.id],
                ),
            )
            assert archived_response.status_code == 400
            assert "归档项目" in archived_response.json()["message"]
            async with transaction(users_database) as session:
                project = await session.get(Project, archived.id)
                assert project is not None and project.managerId is None
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_same_named_users_never_auto_authorize(
    users_database: async_sessionmaker[AsyncSession],
) -> None:
    """A displayName match alone must never grant ownership.

    The name-based recommendation is a UI convenience; only the projects an
    administrator explicitly confirms in ``ownedProjectIds`` are bound.
    """
    async def scenario() -> None:
        identity = await _identity(users_database, ["admin.user.manage", "admin.scope.manage"])
        client = await _client(users_database, identity)
        try:
            project = await _owned_project(users_database, owner_name="李四")
            first = await client.post(
                "/api/admin/users",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四",
                    data_scope="OWNED",
                    owned_ids=[project.id],
                ),
            )
            assert first.status_code == 200
            first_id = uuid.UUID(first.json()["data"]["user"]["id"])

            # A second account with the identical displayName is granted nothing.
            second = await client.post(
                "/api/admin/users",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四",
                    data_scope="OWNED",
                    owned_ids=[],
                ),
            )
            assert second.status_code == 200
            second_id = uuid.UUID(second.json()["data"]["user"]["id"])
            assert second.json()["data"]["user"]["ownedProjectIds"] == []
            assert await _bound_owned_ids(users_database, second_id) == set()
            # The confirmed first account keeps the project exclusively.
            assert await _bound_owned_ids(users_database, first_id) == {project.id}
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_owned_or_assigned_scope_carries_both_project_sources(
    users_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(users_database, ["admin.user.manage", "admin.scope.manage"])
        client = await _client(users_database, identity)
        try:
            owned = await _owned_project(users_database, owner_name="李四")
            assigned = await _owned_project(users_database, owner_name="交付负责人乙")
            created = await client.post(
                "/api/admin/users",
                headers={"origin": "https://web.internal"},
                json=await _manager_payload(
                    users_database,
                    display_name="李四",
                    data_scope="OWNED_OR_ASSIGNED",
                    owned_ids=[owned.id],
                    project_ids=[assigned.id],
                ),
            )
            assert created.status_code == 200
            body = created.json()["data"]["user"]
            assert body["ownedProjectIds"] == [str(owned.id)]
            assert body["assignedProjectIds"] == [str(assigned.id)]
            user_id = uuid.UUID(body["id"])
            assert await _bound_owned_ids(users_database, user_id) == {owned.id}
            async with transaction(users_database) as session:
                bound_assigned = await session.get(Project, assigned.id)
                assert bound_assigned is not None and bound_assigned.managerId is None
        finally:
            await client.aclose()

    asyncio.run(scenario())
