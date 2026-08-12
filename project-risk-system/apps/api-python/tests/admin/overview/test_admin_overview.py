from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.admin.overview.api import get_admin_overview_service, router
from risk_platform.admin.overview.service import (
    AdminOverviewService,
    OverviewDependencyFailure,
    ProviderClient,
)
from risk_platform.ai_providers.client import ConnectionOutcome
from risk_platform.ai_providers.models import AiConnectionStatus, AiProviderConfig
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.models import AuditActorType, AuditLog, AuditResult
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.imports.models import ImportBatch, ImportBatchStatus
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.seed import SeedSettings, seed_reference_data
from risk_platform.shared.crypto import KeyRing, SecretCipher

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def overview_database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL admin overview validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t016_{uuid.uuid4().hex}"
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
            SeedSettings("admin", "系统管理员", "Seed_Admin9!Pass", password_min_length=12),
        )
        user = await session.scalar(select(User).where(User.username == "admin"))
        assert user is not None
        cipher = SecretCipher(KeyRing(active_version="v1", keys={"v1": b"1" * 32}))
        task = DurableTask(
            kind=DurableTaskKind.IMPORT_PREVIEW,
            status=DurableTaskStatus.QUEUED,
            idempotencyKey="t016-preview",
            payload={},
            maxAttempts=1,
        )
        session.add(task)
        await session.flush()
        session.add(
            ImportBatch(
                taskId=task.id,
                fileName="review.xlsx",
                fileHash="a" * 64,
                storageKey="t016/review.xlsx",
                status=ImportBatchStatus.PREVIEWED,
                sheetName="项目清单",
                totalRows=1,
                readyRows=0,
                warningRows=0,
                errorRows=1,
                uploadedById=user.id,
            )
        )
        session.add_all(
            [
                AiProviderConfig(
                    name="failed",
                    vendor="test",
                    endpoint="https://provider.invalid",
                    model="test",
                    encryptedApiKey=cipher.encrypt("test-key-123456").envelope,
                    keyIv="",
                    keyAuthTag="",
                    keyLast4="3456",
                    expiresAt=date.today() + timedelta(days=2),
                    enabled=True,
                    lastTestStatus=AiConnectionStatus.FAILED,
                    createdById=user.id,
                    updatedById=user.id,
                ),
                AuditLog(
                    actorUserId=user.id,
                    actorType=AuditActorType.USER,
                    module="IMPORT",
                    action="PROJECT_IMPORT_IMPORTED",
                    resourceType="IMPORT_BATCH",
                    resourceId="batch",
                    result=AuditResult.SUCCESS,
                    traceId=str(uuid.uuid4()),
                ),
            ]
        )


async def _identity(
    factory: async_sessionmaker[AsyncSession], permissions: list[str]
) -> SessionIdentity:
    async with factory() as session:
        user = await session.scalar(select(User).where(User.username == "admin"))
        assert user is not None
        return SessionIdentity(
            session_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            user=AuthenticatedUser(
                id=str(user.id),
                username=user.username,
                displayName=user.displayName,
                departmentName=None,
                roleCodes=["SYSTEM_ADMIN"],
                permissions=permissions,
                dataScope="ALL",
                mustChangePassword=False,
            ),
        )


class _ProviderClient:
    async def test(self, *_: object) -> ConnectionOutcome:
        return ConnectionOutcome(
            success=False, latency_ms=1, error_code="UPSTREAM_UNREACHABLE", error_summary=None
        )


class _TimeoutProviderClient:
    async def test(self, *_: object) -> ConnectionOutcome:
        return ConnectionOutcome(
            success=False, latency_ms=1, error_code="UPSTREAM_TIMEOUT", error_summary=None
        )


async def _ok() -> None:
    return None


async def _timeout() -> None:
    raise OverviewDependencyFailure("TIMEOUT")


async def _client(
    factory: async_sessionmaker[AsyncSession],
    identity: SessionIdentity,
    *,
    api_check: Callable[[], Awaitable[None]] = _ok,
    database_check: Callable[[], Awaitable[None]] | None = None,
    redis_check: Callable[[], Awaitable[None]] = _ok,
    provider_client: ProviderClient | None = None,
) -> httpx2.AsyncClient:
    service = AdminOverviewService(
        factory,
        SecretCipher(KeyRing(active_version="v1", keys={"v1": b"1" * 32})),
        provider_client or _ProviderClient(),
        api_check=api_check,
        database_check=database_check,
        redis_check=redis_check,
        worker_check=_timeout,
    )

    async def override_identity() -> SessionIdentity:
        return identity

    app = create_app(
        Settings(environment="test", cors_origins=("https://web.internal",)),
        AppComposition(
            routers=(router,),
            dependency_overrides={
                current_identity: override_identity,
                get_admin_overview_service: lambda: service,
            },
        ),
    )
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
    )


def test_overview_returns_real_facts_health_failures_and_safe_contract(
    overview_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        client = await _client(
            overview_database,
            await _identity(
                overview_database,
                ["admin.config.manage", "risk.manage_all", "admin.audit.view"],
            ),
        )
        try:
            response = await client.get("/api/admin/overview")
            assert response.status_code == 200
            data = response.json()["data"]
            assert [item["key"] for item in data["health"]] == [
                "API",
                "DATABASE",
                "REDIS",
                "WORKER",
                "AI_PROVIDER",
            ]
            assert data["health"][3]["code"] == "TIMEOUT"
            assert data["health"][4]["status"] == "UNAVAILABLE"
            assert re.fullmatch(r".+\.\d{3}Z", data["generatedAt"])
            assert re.fullmatch(r".+\.\d{3}Z", data["health"][0]["checkedAt"])
            assert {item["kind"] for item in data["attention"]} >= {
                "IMPORT_REVIEW",
                "AI_PROVIDER_CONNECTION",
                "AI_PROVIDER_EXPIRY",
            }
            statuses = [item["status"] for item in data["attention"]]
            assert statuses == sorted(statuses, key=lambda value: value != "CRITICAL")
            assert data["recentAudit"][0]["actorName"] == "系统管理员"
            assert data["recentAudit"][0]["module"] == "IMPORT"
            assert "encryptedApiKey" not in response.text
            assert data["unavailableSections"] == []
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_production_composition_registers_admin_overview_route() -> None:
    from risk_platform.main import _overview_route_registered, app

    assert _overview_route_registered(app)


def test_overview_returns_section_level_forbidden_partial_data(
    overview_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        client = await _client(
            overview_database, await _identity(overview_database, ["risk.manage_all"])
        )
        try:
            data = (await client.get("/api/admin/overview")).json()["data"]
            assert data["health"] is None and data["recentAudit"] is None
            assert data["attention"] is not None
            assert data["unavailableSections"] == [
                {"section": "health", "reason": "FORBIDDEN", "code": "FORBIDDEN"},
                {"section": "recentAudit", "reason": "FORBIDDEN", "code": "FORBIDDEN"},
            ]
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_health_dependency_timeouts_are_items_not_a_partial_section(
    overview_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        client = await _client(
            overview_database,
            await _identity(overview_database, ["admin.config.manage"]),
            api_check=_timeout,
            database_check=_timeout,
            provider_client=_TimeoutProviderClient(),
        )
        try:
            data = (await client.get("/api/admin/overview")).json()["data"]
            assert data["health"] is not None
            assert [item["code"] for item in data["health"]] == [
                "TIMEOUT",
                "TIMEOUT",
                None,
                "TIMEOUT",
                "TIMEOUT",
            ]
            assert data["unavailableSections"] == [
                {"section": "attention", "reason": "FORBIDDEN", "code": "FORBIDDEN"},
                {"section": "recentAudit", "reason": "FORBIDDEN", "code": "FORBIDDEN"},
            ]
        finally:
            await client.aclose()

    asyncio.run(scenario())
