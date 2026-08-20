from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import create_engine, func, select, text

from risk_platform.admin.models import Department, User
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.rbac.models import Permission, Role, RolePermission, UserRole
from risk_platform.risks.models import RiskCategory
from risk_platform.seed import (
    PERMISSIONS,
    RISK_CATEGORIES,
    RISK_LEVELS,
    ROLES,
    SeedConfigurationError,
    SeedSettings,
    main,
    seed_reference_data,
)
from risk_platform.system_config.models import RiskLevelRule

ROOT = Path(__file__).resolve().parents[1]
VALID_PASSWORD = "Valid_Seed_Pass9!"


@pytest.fixture
def seed_schema() -> Iterator[tuple[str, str]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Seed validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t005_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as connection:
            config = Config(ROOT / "alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
            command.check(config)
        yield sync_url, schema
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _async_url(sync_url: str, schema: str) -> str:
    return f"{sync_url}?options=-csearch_path%3D{schema}"


def _settings() -> SeedSettings:
    return SeedSettings(
        username="admin",
        display_name="系统管理员",
        password=VALID_PASSWORD,
        password_min_length=12,
    )


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"INITIAL_ADMIN_PASSWORD": "short"},
        {"INITIAL_ADMIN_PASSWORD": "NoSpecialCharacter9"},
        {"INITIAL_ADMIN_PASSWORD": "admin_Valid_Pass9!"},
    ],
)
def test_missing_or_weak_password_fails_without_echoing_secret(
    environ: dict[str, str],
) -> None:
    secret = environ.get("INITIAL_ADMIN_PASSWORD")
    with pytest.raises(SeedConfigurationError) as error:
        SeedSettings.from_env(environ)
    if secret is not None:
        assert secret not in str(error.value)


def test_seed_twice_is_repeatable_and_preserves_administrator_password(
    seed_schema: tuple[str, str],
) -> None:
    sync_url, schema = seed_schema

    async def exercise() -> None:
        engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
        factory = create_session_factory(engine)
        async with transaction(factory) as session:
            await seed_reference_data(session, _settings())
        async with transaction(factory) as session:
            administrator = await session.scalar(select(User).where(User.username == "admin"))
            assert administrator is not None
            original_hash = administrator.passwordHash
            assert PasswordHasher().verify(original_hash, VALID_PASSWORD)
            administrator.mustChangePassword = False
        async with transaction(factory) as session:
            await seed_reference_data(session, _settings())
        async with factory() as session:
            administrator = await session.scalar(select(User).where(User.username == "admin"))
            assert administrator is not None
            assert administrator.passwordHash == original_hash
            assert administrator.mustChangePassword is False
            assert await session.scalar(select(func.count()).select_from(Permission)) == 16
            assert await session.scalar(select(func.count()).select_from(Role)) == 4
            assert await session.scalar(select(func.count()).select_from(RiskCategory)) == 8
            assert await session.scalar(select(func.count()).select_from(RiskLevelRule)) == 3
            assert await session.scalar(select(func.count()).select_from(Department)) == 5
            assert await session.scalar(select(func.count()).select_from(User)) == 1
            assert await session.scalar(select(func.count()).select_from(UserRole)) == 1
        await engine.dispose()

    asyncio.run(exercise())


def test_seed_has_exact_approved_roles_permissions_and_scopes(
    seed_schema: tuple[str, str],
) -> None:
    sync_url, schema = seed_schema

    async def exercise() -> None:
        engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
        factory = create_session_factory(engine)
        async with transaction(factory) as session:
            await seed_reference_data(session, _settings())
        async with factory() as session:
            permissions = set((await session.scalars(select(Permission.code))).all())
            assert permissions == {definition[0] for definition in PERMISSIONS}
            roles = (await session.scalars(select(Role))).all()
            assert {role.code: role.defaultDataScope for role in roles} == {
                definition[0]: definition[3] for definition in ROLES
            }
            for code, _name, _description, _scope, expected_permissions in ROLES:
                actual = set(
                    (
                        await session.scalars(
                            select(Permission.code)
                            .join(RolePermission)
                            .join(Role)
                            .where(Role.code == code)
                        )
                    ).all()
                )
                assert actual == set(expected_permissions)
            assert {item.value for item in type(roles[0].defaultDataScope)} == {
                "ALL",
                "OWNED",
                "ASSIGNED",
                "OWNED_OR_ASSIGNED",
                "NONE",
            }
        await engine.dispose()

    asyncio.run(exercise())


def test_seed_reference_values_match_approved_sources(seed_schema: tuple[str, str]) -> None:
    sync_url, schema = seed_schema

    async def exercise() -> None:
        engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
        factory = create_session_factory(engine)
        async with transaction(factory) as session:
            await seed_reference_data(session, _settings())
        async with factory() as session:
            categories = (await session.scalars(select(RiskCategory))).all()
            assert {(item.code, item.name, item.sortOrder) for item in categories} == {
                (code, name, sort_order)
                for code, name, _keywords, sort_order in RISK_CATEGORIES
            }
            levels = (await session.scalars(select(RiskLevelRule))).all()
            assert {(item.level, item.displayName, item.colorToken) for item in levels} == {
                (level, display_name, color)
                for level, display_name, color, _criteria, _keywords, _sort_order in RISK_LEVELS
            }
        await engine.dispose()

    asyncio.run(exercise())


def test_seed_uses_one_caller_owned_transaction_for_rollback(
    seed_schema: tuple[str, str],
) -> None:
    sync_url, schema = seed_schema

    async def exercise() -> None:
        engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
        factory = create_session_factory(engine)
        with pytest.raises(RuntimeError, match="injected failure"):
            async with transaction(factory) as session:
                await seed_reference_data(session, _settings())
                raise RuntimeError("injected failure")
        async with factory() as session:
            # Only the migration-granted agent.scope.manage permission remains;
            # every seed-owned insert must have rolled back.
            assert await session.scalar(select(func.count()).select_from(Permission)) == 1
            assert await session.scalar(select(func.count()).select_from(User)) == 0
        await engine.dispose()

    asyncio.run(exercise())


def test_seed_cli_output_never_contains_password(
    seed_schema: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sync_url, schema = seed_schema
    monkeypatch.setenv("DATABASE_URL", _async_url(sync_url, schema))
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("INITIAL_ADMIN_DISPLAY_NAME", "系统管理员")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", VALID_PASSWORD)

    main([])

    captured = capsys.readouterr()
    assert "Seed completed" in captured.out
    assert VALID_PASSWORD not in captured.out
    assert VALID_PASSWORD not in captured.err
