from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, cast

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import APIRouter, Depends
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.models import AuditLog
from risk_platform.auth.api import get_auth_service, require_password_changed, router
from risk_platform.auth.models import Session
from risk_platform.auth.policy import password_policy_violations
from risk_platform.auth.service import (
    AuthConfiguration,
    AuthService,
    SessionIdentity,
    SessionKey,
    SessionKeyError,
)
from risk_platform.config import Settings, SettingsError
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.rbac.models import (
    DataScopeType,
    Permission,
    Role,
    RolePermission,
    UserRole,
)

ROOT = Path(__file__).resolve().parents[2]
CURRENT_PASSWORD = "Initial_Strong9!"
NEW_PASSWORD = "Changed_Strong8!"
TEST_SESSION_KEY = SessionKey(bytes([9]) * 32)


@pytest.fixture(scope="module")
def auth_database() -> Iterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL auth validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t009_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(
        sync_url,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()

    async_url = f"{sync_url}?options=-csearch_path%3D{schema}"
    engine = create_database_engine(async_url, pool_pre_ping=False)
    factory = create_session_factory(engine)
    try:
        yield engine, factory
    finally:
        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_password_policy_matches_frontend_and_legacy_contract() -> None:
    assert password_policy_violations(
        "Risk@2026Strong", minimum_length=12, username="admin"
    ) == ()
    violations = password_policy_violations("admin", minimum_length=12, username="admin")
    assert violations == (
        "密码长度至少为 12 位",
        "密码需包含大写字母",
        "密码需包含数字",
        "密码需包含特殊字符",
        "密码不能包含登录账号",
    )


def test_auth_configuration_reads_owned_environment_without_echoing_values() -> None:
    configuration = AuthConfiguration.from_env(
        {
            "SESSION_TTL_HOURS": "12",
            "LOGIN_MAX_ATTEMPTS": "7",
            "LOGIN_LOCK_MINUTES": "45",
            "PASSWORD_MIN_LENGTH": "16",
        }
    )
    assert configuration == AuthConfiguration(
        session_hours=12,
        login_max_attempts=7,
        login_lock_minutes=45,
        password_min_length=16,
    )
    with pytest.raises(ValueError) as error:
        AuthConfiguration.from_env({"LOGIN_MAX_ATTEMPTS": "secret-invalid-value"})
    assert "LOGIN_MAX_ATTEMPTS" in str(error.value)
    assert "secret-invalid-value" not in str(error.value)


def test_session_key_requires_a_valid_explicit_file(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-session-key"
    short = tmp_path / "short-session-key"
    valid = tmp_path / "valid-session-key"
    short.write_bytes(b"too-short")
    valid.write_bytes(b"0123456789abcdef0123456789abcdef\n")

    with pytest.raises(SessionKeyError, match="SESSION_KEY_LOAD_FAILED"):
        SessionKey.from_file(missing)
    with pytest.raises(SessionKeyError, match="SESSION_KEY_TOO_SHORT"):
        SessionKey.from_file(short)
    assert SessionKey.from_file(valid).digest(b"test", "value")

    with pytest.raises(SettingsError, match="SESSION_SECRET_FILE"):
        Settings.from_env({"NODE_ENV": "production"})
    configured = Settings.from_env(
        {"NODE_ENV": "production", "SESSION_SECRET_FILE": str(valid)}
    )
    loaded = SessionKey.from_settings(configured)
    assert loaded.digest(b"session-token", "same-value") != loaded.digest(
        b"client-ip", "same-value"
    )
    assert loaded.digest(b"session-token", "same-value") != SessionKey(
        bytes([8]) * 32
    ).digest(b"session-token", "same-value")


async def _create_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    must_change_password: bool = True,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    async with transaction(factory) as session:
        department = Department(code=f"D-{username}", name="技术管理部")
        session.add(department)
        role = await session.scalar(select(Role).where(Role.code == "PROJECT_MANAGER"))
        if role is None:
            role = Role(
                code="PROJECT_MANAGER",
                name="项目负责人",
                defaultDataScope=DataScopeType.ASSIGNED,
            )
            session.add(role)
        permission = await session.scalar(
            select(Permission).where(Permission.code == "dashboard.view")
        )
        if permission is None:
            permission = Permission(code="dashboard.view", name="查看看板", module="DASHBOARD")
            session.add(permission)
        await session.flush()
        user = User(
            username=username,
            passwordHash=PasswordHasher().hash(CURRENT_PASSWORD),
            displayName="测试用户",
            departmentId=department.id,
            status=status,
            mustChangePassword=must_change_password,
        )
        session.add(user)
        await session.flush()
        session.add(
            UserRole(userId=user.id, roleId=role.id, dataScope=DataScopeType.ASSIGNED)
        )
        role_permission = await session.get(RolePermission, (role.id, permission.id))
        if role_permission is None:
            session.add(RolePermission(roleId=role.id, permissionId=permission.id))
        return user


def _test_app(service: AuthService, *, production: bool = True):  # type: ignore[no-untyped-def]
    protected = APIRouter(prefix="/protected")

    @protected.get("/value")
    async def protected_value(
        identity: Annotated[SessionIdentity, Depends(require_password_changed)],
    ) -> dict[str, str]:
        return {"userId": identity.user.id}

    def override_service() -> AuthService:
        return service

    return create_app(
        Settings(
            environment="production" if production else "test",
            cors_origins=("https://web.internal",),
            session_secret_file=Path("/run/secrets/project_risk_session_key")
            if production
            else None,
        ),
        AppComposition(
            routers=(router, protected),
            dependency_overrides={get_auth_service: override_service},
        ),
    )


async def _client(service: AuthService) -> AsyncIterator[httpx2.AsyncClient]:
    app = _test_app(service)
    transport = httpx2.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=transport, base_url="https://testserver") as client,
    ):
        yield client


async def _login(client: httpx2.AsyncClient, username: str, password: str):  # type: ignore[no-untyped-def]
    return await client.post(
        "/api/auth/login",
        headers={"origin": "https://web.internal"},
        json={"username": username, "password": password},
    )


def test_login_forced_change_revocation_and_cookie_contract(
    auth_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = auth_database
    username = f"user-{uuid.uuid4().hex[:8]}"

    async def scenario() -> None:
        user = await _create_user(factory, username=username)
        service = AuthService(factory, TEST_SESSION_KEY)
        async for client in _client(service):
            login = await _login(client, f"  {username.upper()}  ", CURRENT_PASSWORD)
            assert login.status_code == 200
            assert login.json()["message"] == "登录成功，请先修改初始密码"
            login_expiration = login.json()["data"]["expiresAt"]
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", login_expiration)
            assert login.json()["data"]["user"] == {
                "id": str(user.id),
                "username": username,
                "displayName": "测试用户",
                "departmentName": "技术管理部",
                "roleCodes": ["PROJECT_MANAGER"],
                "permissions": ["dashboard.view"],
                "dataScope": "ASSIGNED",
                "mustChangePassword": True,
                "authMethod": "PASSWORD",
            }
            cookie = login.headers["set-cookie"]
            assert "project_risk_session=" in cookie
            assert "HttpOnly" in cookie
            assert "Secure" in cookie
            assert "SameSite=lax" in cookie
            assert "Path=/" in cookie
            assert "expires=" in cookie.lower()
            assert service.hash_token(client.cookies["project_risk_session"])

            session_response = await client.get("/api/auth/session")
            assert session_response.status_code == 200
            assert session_response.json()["data"]["expiresAt"] == login_expiration
            forced = await client.get("/api/protected/value")
            assert (forced.status_code, forced.json()["message"]) == (403, "请先修改初始密码")

            changed = await client.post(
                "/api/auth/change-password",
                headers={"origin": "https://web.internal"},
                json={
                    "currentPassword": CURRENT_PASSWORD,
                    "newPassword": NEW_PASSWORD,
                    "confirmPassword": NEW_PASSWORD,
                },
            )
            assert changed.status_code == 200
            assert changed.json()["data"] == {"reloginRequired": True}
            assert "Max-Age=0" in changed.headers["set-cookie"]

            old_login = await _login(client, username, CURRENT_PASSWORD)
            assert old_login.status_code == 401
            new_login = await _login(client, username, NEW_PASSWORD)
            assert new_login.status_code == 200
            assert new_login.json()["data"]["user"]["mustChangePassword"] is False
            protected = await client.get("/api/protected/value")
            assert protected.status_code == 200

            logout = await client.post(
                "/api/auth/logout", headers={"origin": "https://web.internal"}
            )
            assert logout.status_code == 200
            assert logout.json()["data"] is None
            assert "Max-Age=0" in logout.headers["set-cookie"]
            assert (await client.get("/api/auth/session")).status_code == 401

        async with factory() as session:
            sessions = (
                await session.scalars(select(Session).where(Session.userId == user.id))
            ).all()
            assert sessions
            assert all(row.revokedAt is not None for row in sessions)
            assert all(len(row.tokenHash) == 64 for row in sessions)
            assert all(CURRENT_PASSWORD not in row.tokenHash for row in sessions)

    asyncio.run(scenario())


def test_negative_origin_validation_and_failed_actions_are_audited(
    auth_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = auth_database
    username = f"negative-{uuid.uuid4().hex[:8]}"

    async def scenario() -> None:
        user = await _create_user(factory, username=username, must_change_password=False)
        service = AuthService(factory, TEST_SESSION_KEY)
        async for client in _client(service):
            unknown_field = await client.post(
                "/api/auth/login",
                json={"username": username, "password": CURRENT_PASSWORD, "extra": "rejected"},
            )
            assert unknown_field.status_code == 422
            wrong = await _login(client, username, "wrong-password")
            assert (wrong.status_code, wrong.json()["message"]) == (401, "账号或密码错误")
            unknown = await _login(client, f"missing-{uuid.uuid4().hex}", "wrong-password")
            assert unknown.status_code == 401
            success = await _login(client, username, CURRENT_PASSWORD)
            assert success.status_code == 200
            rejected_origin = await client.post(
                "/api/auth/logout", headers={"origin": "https://evil.invalid"}
            )
            assert (rejected_origin.status_code, rejected_origin.json()["message"]) == (
                403,
                "请求来源校验失败",
            )
            still_valid = await client.get("/api/auth/session")
            assert still_valid.status_code == 200
            bad_change = await client.post(
                "/api/auth/change-password",
                json={
                    "currentPassword": "wrong-password",
                    "newPassword": NEW_PASSWORD,
                    "confirmPassword": NEW_PASSWORD,
                },
            )
            assert (bad_change.status_code, bad_change.json()["message"]) == (
                400,
                "当前密码不正确",
            )
        async with factory() as session:
            failures = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.result == "FAILURE", AuditLog.resourceId == str(user.id))
            )
            assert failures == 2

    asyncio.run(scenario())


def test_lock_expiry_and_concurrent_failures_are_serialized(
    auth_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = auth_database
    username = f"lock-{uuid.uuid4().hex[:8]}"

    async def scenario() -> None:
        user = await _create_user(factory, username=username, must_change_password=False)
        service = AuthService(
            factory,
            TEST_SESSION_KEY,
            AuthConfiguration(login_max_attempts=3, login_lock_minutes=30),
        )
        app = _test_app(service)

        async def fail_once() -> int:
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                return cast(int, (await _login(client, username, "wrong-password")).status_code)

        async with app.router.lifespan_context(app):
            results = list(await asyncio.gather(fail_once(), fail_once(), fail_once()))
            assert results == [401, 401, 401]
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                locked = await _login(client, username, CURRENT_PASSWORD)
                assert locked.status_code == 423
                assert locked.json()["code"] == "ACCOUNT_LOCKED"

        async with transaction(factory) as session:
            locked_user = await session.get(User, user.id)
            assert locked_user is not None
            assert locked_user.failedLoginCount == 3
            assert locked_user.status is UserStatus.LOCKED
            locked_user.lockedUntil = datetime.now(UTC) - timedelta(seconds=1)

        async for client in _client(service):
            recovered = await _login(client, username, CURRENT_PASSWORD)
            assert recovered.status_code == 200
        async with factory() as session:
            recovered_user = await session.get(User, user.id)
            assert recovered_user is not None
            assert recovered_user.status is UserStatus.ACTIVE
            assert recovered_user.failedLoginCount == 0
            assert recovered_user.lockedUntil is None

    asyncio.run(scenario())
