from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.ai_providers.models import (
    AiConnectionStatus,
    AiModelConfig,
    AiModelHealth,
    AiProviderAccount,
    AiProviderAccountHealth,
    AiProviderConfig,
    AiProviderProtocol,
    AiProviderType,
    AiProviderV2CallLog,
)
from risk_platform.ai_providers.v2_adapter import (
    AiProviderAdapter,
    ProviderCandidate,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorClassification,
    ProviderFinishReason,
    ProviderModelInfo,
    ProviderTokenUsage,
)
from risk_platform.ai_providers.v2_api import get_ai_provider_v2_service, router
from risk_platform.ai_providers.v2_schemas import (
    CreateModelConfigRequest,
    CreateProviderAccountRequest,
    ModelConfigStatusRequest,
)
from risk_platform.ai_providers.v2_service import AiProviderV2Service, ProviderV2Runtime
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.models import AuditLog
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.shared.crypto import KeyRing, SecretCipher

ROOT = Path(__file__).resolve().parents[2]


class SuccessfulAdapter(AiProviderAdapter):
    async def list_models(
        self, encrypted_api_key: str, timeout_seconds: int
    ) -> tuple[ProviderModelInfo, ...]:
        del encrypted_api_key, timeout_seconds
        return (ProviderModelInfo("model-a"), ProviderModelInfo("model-b"))

    async def chat(
        self, candidate: ProviderCandidate, request: ProviderChatRequest
    ) -> ProviderChatResponse:
        del candidate, request
        return ProviderChatResponse(
            "ok", (), ProviderFinishReason.STOP, ProviderTokenUsage(1, 1, 2), 1
        )


class AuthenticationFailureAdapter(SuccessfulAdapter):
    async def list_models(
        self, encrypted_api_key: str, timeout_seconds: int
    ) -> tuple[ProviderModelInfo, ...]:
        del encrypted_api_key, timeout_seconds
        raise ProviderError(
            ProviderErrorClassification.AUTHENTICATION,
            retryable=False,
            failover_allowed=False,
            status_code=401,
        )


@pytest.fixture(scope="module")
def provider_v2_database() -> Iterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; Provider V2 PostgreSQL validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t048_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
        command.check(config)
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


def _cipher() -> SecretCipher:
    return SecretCipher(KeyRing(active_version="v1", keys={"v1": b"p" * 32}))


def _identity(user_id: UUID) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(user_id),
            username="t048-admin",
            displayName="T048 Admin",
            departmentName=None,
            roleCodes=["SYSTEM_ADMIN"],
            permissions=["admin.ai.manage"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


async def _seed_user(factory: async_sessionmaker[AsyncSession]) -> UUID:
    async with transaction(factory) as session:
        existing = await session.scalar(select(User).where(User.username == "t048-admin"))
        if existing is not None:
            return existing.id
        row = User(
            username="t048-admin",
            passwordHash="not-a-real-password-hash",
            displayName="T048 Admin",
            mustChangePassword=False,
        )
        session.add(row)
        await session.flush()
        return row.id


def test_account_secret_is_encrypted_and_legacy_table_is_not_dual_written(
    provider_v2_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = provider_v2_database
    cipher = _cipher()

    async def scenario() -> None:
        user_id = await _seed_user(factory)
        async with transaction(factory) as session:
            legacy = AiProviderConfig(
                name="legacy-provider",
                vendor="legacy-company",
                endpoint="https://legacy.example.test",
                protocol=AiProviderProtocol.OPENAI_CHAT_COMPLETIONS,
                model="legacy-model",
                encryptedApiKey=cipher.encrypt("legacy-secret-key").envelope,
                keyIv="",
                keyAuthTag="",
                keyLast4="-key",
                timeoutSeconds=60,
                retryCount=2,
                enabled=True,
                isDefault=True,
                priority=100,
                lastTestStatus=AiConnectionStatus.UNTESTED,
            )
            session.add(legacy)
        service = AiProviderV2Service(factory, cipher, SuccessfulAdapter())
        response = await service.create_account(
            CreateProviderAccountRequest(
                name="DeepSeek Official",
                apiKey="sk-plain-secret-must-not-persist",
                enabled=True,
            ),
            _identity(user_id),
            uuid.uuid4(),
        )
        async with factory() as session:
            account = await session.scalar(
                select(AiProviderAccount).where(AiProviderAccount.id == UUID(response.id))
            )
            assert account is not None
            assert "sk-plain-secret-must-not-persist" not in account.encryptedApiKey
            assert cipher.decrypt(account.encryptedApiKey) == "sk-plain-secret-must-not-persist"
            assert await session.scalar(select(func.count()).select_from(AiProviderConfig)) == 1
        serialized = response.model_dump()
        assert "encryptedApiKey" not in serialized
        assert "apiKey" not in serialized
        assert response.maskedKey.endswith("sist")

    asyncio.run(scenario())


def test_account_one_to_many_stable_ordering_exclusion_and_snapshot_refresh(
    provider_v2_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = provider_v2_database

    async def scenario() -> None:
        async with transaction(factory) as session:
            account = await session.scalar(
                select(AiProviderAccount).where(AiProviderAccount.name == "DeepSeek Official")
            )
            assert account is not None
            account.health = AiProviderAccountHealth.UNTESTED
            models = [
                AiModelConfig(
                    id=UUID(int=10),
                    accountId=account.id,
                    modelName="priority-tie-b",
                    enabled=True,
                    isDefault=False,
                    priority=20,
                ),
                AiModelConfig(
                    id=UUID(int=9),
                    accountId=account.id,
                    modelName="priority-tie-a",
                    enabled=True,
                    isDefault=False,
                    priority=20,
                ),
                AiModelConfig(
                    id=UUID(int=11),
                    accountId=account.id,
                    modelName="default-model",
                    enabled=True,
                    isDefault=True,
                    priority=999,
                ),
                AiModelConfig(
                    id=UUID(int=12),
                    accountId=account.id,
                    modelName="disabled-model",
                    enabled=False,
                    isDefault=False,
                    priority=1,
                ),
                AiModelConfig(
                    id=UUID(int=13),
                    accountId=account.id,
                    modelName="config-error-model",
                    enabled=True,
                    isDefault=False,
                    priority=1,
                    health=AiModelHealth.CONFIG_ERROR,
                ),
            ]
            session.add_all(models)
        runtime = ProviderV2Runtime(factory, SuccessfulAdapter())
        snapshot = await runtime.candidate_snapshot()
        assert [item.model_name for item in snapshot] == [
            "default-model",
            "priority-tie-a",
            "priority-tie-b",
        ]
        assert len({item.account_id for item in snapshot}) == 1
        async with transaction(factory) as session:
            row = await session.get(AiModelConfig, UUID(int=10))
            assert row is not None
            row.priority = 0
            old_default = await session.get(AiModelConfig, UUID(int=11))
            assert old_default is not None
            old_default.isDefault = False
            await session.flush()
            row.isDefault = True
        assert [item.model_name for item in snapshot] == [
            "default-model",
            "priority-tie-a",
            "priority-tie-b",
        ]
        refreshed = await runtime.candidate_snapshot()
        assert [item.model_name for item in refreshed] == [
            "priority-tie-b",
            "priority-tie-a",
            "default-model",
        ]

    asyncio.run(scenario())


def test_postgresql_enforces_one_enabled_default_per_account(
    provider_v2_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = provider_v2_database

    async def scenario() -> None:
        with pytest.raises(IntegrityError):
            async with transaction(factory) as session:
                account_id = await session.scalar(
                    select(AiProviderAccount.id).where(
                        AiProviderAccount.name == "DeepSeek Official"
                    )
                )
                assert account_id is not None
                session.add(
                    AiModelConfig(
                        accountId=account_id,
                        modelName="second-default",
                        enabled=True,
                        isDefault=True,
                        priority=2,
                    )
                )

    asyncio.run(scenario())


def test_account_and_model_health_are_separate_and_transient_errors_do_not_pollute(
    provider_v2_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = provider_v2_database

    async def scenario() -> None:
        runtime = ProviderV2Runtime(factory, SuccessfulAdapter())
        snapshot = await runtime.candidate_snapshot()
        candidate = next(item for item in snapshot if item.model_name == "priority-tie-b")
        transient = ProviderError(
            ProviderErrorClassification.TRANSIENT_SERVER,
            retryable=True,
            failover_allowed=True,
            status_code=503,
        )
        await runtime._record_health_error(candidate, transient)
        async with factory() as session:
            account = await session.get(AiProviderAccount, candidate.account_id)
            model = await session.get(AiModelConfig, candidate.model_config_id)
            assert account is not None and model is not None
            assert account.health is AiProviderAccountHealth.UNTESTED
            assert model.health is AiModelHealth.UNTESTED
        missing = ProviderError(
            ProviderErrorClassification.MODEL_NOT_FOUND,
            retryable=False,
            failover_allowed=True,
            status_code=404,
        )
        await runtime._record_health_error(candidate, missing)
        async with factory() as session:
            account = await session.get(AiProviderAccount, candidate.account_id)
            model = await session.get(AiModelConfig, candidate.model_config_id)
            assert account is not None and model is not None
            assert account.health is AiProviderAccountHealth.UNTESTED
            assert model.health is AiModelHealth.CONFIG_ERROR
        auth = ProviderError(
            ProviderErrorClassification.AUTHENTICATION,
            retryable=False,
            failover_allowed=False,
            status_code=401,
        )
        await runtime._record_health_error(candidate, auth)
        async with factory() as session:
            account = await session.get(AiProviderAccount, candidate.account_id)
            model = await session.get(AiModelConfig, candidate.model_config_id)
            assert account is not None and model is not None
            assert account.health is AiProviderAccountHealth.CREDENTIAL_ERROR
            assert model.health is AiModelHealth.CONFIG_ERROR

    asyncio.run(scenario())


def test_provider_type_is_closed_to_deepseek_official() -> None:
    assert [item.value for item in AiProviderType] == ["DEEPSEEK_OFFICIAL"]


def test_v2_call_log_schema_is_metadata_only() -> None:
    columns = set(AiProviderV2CallLog.__table__.columns.keys())
    assert columns.isdisjoint(
        {
            "apiKey",
            "authorization",
            "prompt",
            "messages",
            "toolArguments",
            "toolResult",
            "requestBody",
            "responseBody",
        }
    )


def test_admin_v2_service_manages_models_and_switches_default_safely(
    provider_v2_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = provider_v2_database

    async def scenario() -> None:
        user_id = await _seed_user(factory)
        identity = _identity(user_id)
        service = AiProviderV2Service(factory, _cipher(), SuccessfulAdapter())
        account = await service.create_account(
            CreateProviderAccountRequest(
                name="DeepSeek CRUD Account",
                apiKey="sk-crud-secret-value",
            ),
            identity,
            uuid.uuid4(),
        )
        account_id = UUID(account.id)
        first = await service.create_model(
            account_id,
            CreateModelConfigRequest(
                modelName="model-first", isDefault=True, priority=100
            ),
            identity,
            uuid.uuid4(),
        )
        second = await service.create_model(
            account_id,
            CreateModelConfigRequest(
                modelName="model-second", isDefault=False, priority=10
            ),
            identity,
            uuid.uuid4(),
        )
        switched = await service.set_default_model(
            account_id, UUID(second.id), identity, uuid.uuid4()
        )
        assert switched.isDefault
        rows = await service.list_models(account_id)
        assert [row.modelName for row in rows] == ["model-second", "model-first"]
        disabled = await service.set_model_status(
            account_id,
            UUID(second.id),
            ModelConfigStatusRequest(enabled=False),
            identity,
            uuid.uuid4(),
        )
        assert not disabled.enabled
        assert not disabled.isDefault
        reset = await service.set_default_model(
            account_id, UUID(first.id), identity, uuid.uuid4()
        )
        assert reset.isDefault

    asyncio.run(scenario())


def test_failed_account_test_updates_health_and_writes_sensitive_audit(
    provider_v2_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = provider_v2_database

    async def scenario() -> None:
        user_id = await _seed_user(factory)
        identity = _identity(user_id)
        service = AiProviderV2Service(factory, _cipher(), AuthenticationFailureAdapter())
        account = await service.create_account(
            CreateProviderAccountRequest(
                name="DeepSeek Failed Test Account",
                apiKey="sk-failed-test-secret",
            ),
            identity,
            uuid.uuid4(),
        )
        account_id = UUID(account.id)
        result = await service.test_account(account_id, identity, uuid.uuid4())
        assert not result.success
        assert result.errorClassification == ProviderErrorClassification.AUTHENTICATION.value
        async with factory() as session:
            stored = await session.get(AiProviderAccount, account_id)
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.resourceId == str(account_id),
                    AuditLog.action == "AI_PROVIDER_V2_MODELS_DISCOVERY_FAILED",
                )
            )
            assert stored is not None
            assert stored.health is AiProviderAccountHealth.CREDENTIAL_ERROR
            assert audit is not None

    asyncio.run(scenario())


def test_account_test_http_endpoint_preserves_missing_resource_404(
    provider_v2_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = provider_v2_database

    async def scenario() -> None:
        identity = _identity(await _seed_user(factory))
        service = AiProviderV2Service(factory, _cipher(), SuccessfulAdapter())

        async def override_identity() -> SessionIdentity:
            return identity

        app = create_app(
            Settings(environment="test", cors_origins=("https://web.internal",)),
            AppComposition(
                routers=(router,),
                dependency_overrides={
                    current_identity: override_identity,
                    get_ai_provider_v2_service: lambda: service,
                },
            ),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            response = await client.post(
                f"/api/admin/ai-provider-v2/accounts/{uuid.uuid4()}/test",
                headers={"origin": "https://web.internal"},
            )
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"

    asyncio.run(scenario())
