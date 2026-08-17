"""Provider V2 administration, immutable candidate snapshots and failover runtime."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.models import (
    AiCallResult,
    AiModelConfig,
    AiModelHealth,
    AiProviderAccount,
    AiProviderAccountHealth,
    AiProviderType,
    AiProviderV2CallLog,
)
from risk_platform.ai_providers.v2_adapter import (
    AiProviderAdapter,
    ProviderCandidate,
    ProviderCandidatesExhausted,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorClassification,
    ProviderMessage,
    ProviderRole,
    ProviderType,
)
from risk_platform.ai_providers.v2_schemas import (
    CreateModelConfigRequest,
    CreateProviderAccountRequest,
    DiscoveredModelResponse,
    ModelConfigResponse,
    ModelConfigStatusRequest,
    ProviderAccountResponse,
    ProviderAccountStatusRequest,
    ProviderV2ConnectionResult,
    RotateProviderAccountKeyRequest,
    UpdateModelConfigRequest,
    UpdateProviderAccountRequest,
)
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.shared.crypto import SecretCipher
from risk_platform.shared.errors import ApiError

TRANSPORT_RETRY_COUNT = 2
BACKOFF_BASE_SECONDS = 0.25
BACKOFF_JITTER_SECONDS = 0.1

Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[float, float], float]


class ProviderV2Runtime:
    """Execute one stable candidate snapshot without leaking vendor wire details."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        adapter: AiProviderAdapter,
        *,
        sleep: Sleep = asyncio.sleep,
        jitter: Jitter = random.uniform,
    ) -> None:
        self._session_factory = session_factory
        self._adapter = adapter
        self._sleep = sleep
        self._jitter = jitter

    async def candidate_snapshot(self) -> tuple[ProviderCandidate, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(AiProviderAccount, AiModelConfig)
                    .join(AiModelConfig, AiModelConfig.accountId == AiProviderAccount.id)
                    .where(
                        AiProviderAccount.providerType == AiProviderType.DEEPSEEK_OFFICIAL,
                        AiProviderAccount.enabled.is_(True),
                        AiProviderAccount.health != AiProviderAccountHealth.CREDENTIAL_ERROR,
                        AiModelConfig.enabled.is_(True),
                        AiModelConfig.health != AiModelHealth.CONFIG_ERROR,
                    )
                    .order_by(
                        AiModelConfig.isDefault.desc(),
                        AiModelConfig.priority.asc(),
                        AiModelConfig.id.asc(),
                    )
                )
            ).all()
            return tuple(
                ProviderCandidate(
                    account_id=account.id,
                    account_name=account.name,
                    provider_type=ProviderType(account.providerType.value),
                    model_config_id=model.id,
                    model_name=model.modelName,
                    timeout_seconds=model.timeoutSeconds,
                    encrypted_api_key=account.encryptedApiKey,
                )
                for account, model in rows
            )

    async def chat(self, request: ProviderChatRequest) -> ProviderChatResponse:
        return await self.chat_snapshot(await self.candidate_snapshot(), request)

    async def chat_snapshot(
        self, candidates: tuple[ProviderCandidate, ...], request: ProviderChatRequest
    ) -> ProviderChatResponse:
        """Run against the immutable candidates captured by one Agent execution."""
        if not candidates:
            raise ProviderError(
                ProviderErrorClassification.CREDENTIAL_UNAVAILABLE,
                retryable=False,
                failover_allowed=False,
            )
        last_error: ProviderError | None = None
        for candidate in candidates:
            for attempt in range(TRANSPORT_RETRY_COUNT + 1):
                started = monotonic()
                try:
                    response = await self._adapter.chat(candidate, request)
                except ProviderError as error:
                    last_error = error
                    await self._record_failure(candidate, error, started)
                    await self._record_health_error(candidate, error)
                    if error.retryable and attempt < TRANSPORT_RETRY_COUNT:
                        await self._sleep(self._retry_delay(error, attempt + 1))
                        continue
                    if error.failover_allowed:
                        break
                    raise
                await self._record_success(candidate, response)
                return response
        assert last_error is not None
        raise ProviderCandidatesExhausted(last_error)

    def _retry_delay(self, error: ProviderError, retry_number: int) -> float:
        exponential = BACKOFF_BASE_SECONDS * (2 ** (retry_number - 1))
        jitter = float(self._jitter(0.0, BACKOFF_JITTER_SECONDS))
        delay = float(exponential + jitter)
        retry_after = float(error.retry_after_seconds or 0.0)
        return retry_after if retry_after > delay else delay

    async def _record_success(
        self, candidate: ProviderCandidate, response: ProviderChatResponse
    ) -> None:
        async with transaction(self._session_factory) as session:
            account = await session.get(AiProviderAccount, candidate.account_id)
            model = await session.get(AiModelConfig, candidate.model_config_id)
            now = datetime.now(UTC)
            if account is not None:
                account.health = AiProviderAccountHealth.AVAILABLE
                account.lastHealthAt = now
                account.lastHealthErrorCode = None
            if model is not None:
                model.health = AiModelHealth.AVAILABLE
                model.lastHealthAt = now
                model.lastHealthErrorCode = None
            session.add(
                AiProviderV2CallLog(
                    accountId=candidate.account_id,
                    modelConfigId=candidate.model_config_id,
                    accountNameSnapshot=candidate.account_name,
                    modelNameSnapshot=candidate.model_name,
                    durationMs=response.latency_ms,
                    inputTokens=response.usage.input_tokens,
                    outputTokens=response.usage.output_tokens,
                    totalTokens=response.usage.total_tokens,
                    result=AiCallResult.SUCCESS,
                )
            )

    async def _record_failure(
        self, candidate: ProviderCandidate, error: ProviderError, started: float
    ) -> None:
        async with transaction(self._session_factory) as session:
            session.add(
                AiProviderV2CallLog(
                    accountId=candidate.account_id,
                    modelConfigId=candidate.model_config_id,
                    accountNameSnapshot=candidate.account_name,
                    modelNameSnapshot=candidate.model_name,
                    httpStatus=error.status_code,
                    durationMs=max(0, round((monotonic() - started) * 1000)),
                    result=AiCallResult.FAILURE,
                    errorClassification=error.classification.value,
                )
            )

    async def _record_health_error(
        self, candidate: ProviderCandidate, error: ProviderError
    ) -> None:
        account_error = error.classification in {
            ProviderErrorClassification.AUTHENTICATION,
            ProviderErrorClassification.PERMISSION,
            ProviderErrorClassification.CREDENTIAL_UNAVAILABLE,
        }
        model_error = error.classification is ProviderErrorClassification.MODEL_NOT_FOUND
        if not account_error and not model_error:
            return
        async with transaction(self._session_factory) as session:
            now = datetime.now(UTC)
            if account_error:
                account = await session.get(AiProviderAccount, candidate.account_id)
                if account is not None:
                    account.health = AiProviderAccountHealth.CREDENTIAL_ERROR
                    account.lastHealthAt = now
                    account.lastHealthErrorCode = error.classification.value
            if model_error:
                model = await session.get(AiModelConfig, candidate.model_config_id)
                if model is not None:
                    model.health = AiModelHealth.CONFIG_ERROR
                    model.lastHealthAt = now
                    model.lastHealthErrorCode = error.classification.value


class AiProviderV2Service:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        adapter: AiProviderAdapter,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._adapter = adapter

    async def list_accounts(self) -> list[ProviderAccountResponse]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(AiProviderAccount).order_by(
                        AiProviderAccount.createdAt.asc(), AiProviderAccount.id.asc()
                    )
                )
            ).all()
            counts = await self._model_counts(session)
            return [self._account_response(row, counts.get(row.id, 0)) for row in rows]

    async def create_account(
        self, payload: CreateProviderAccountRequest, identity: SessionIdentity, trace_id: UUID
    ) -> ProviderAccountResponse:
        async with transaction(self._session_factory) as session:
            await self._account_name_available(session, payload.name)
            encrypted = self._cipher.encrypt(payload.apiKey)
            row = AiProviderAccount(
                name=payload.name,
                providerType=AiProviderType.DEEPSEEK_OFFICIAL,
                encryptedApiKey=encrypted.envelope,
                keyLast4=payload.apiKey[-4:],
                enabled=payload.enabled,
                createdById=UUID(identity.user.id),
                updatedById=UUID(identity.user.id),
            )
            session.add(row)
            await session.flush()
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_ACCOUNT_CREATED", row.id)
            return self._account_response(row, 0)

    async def update_account(
        self,
        account_id: UUID,
        payload: UpdateProviderAccountRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ProviderAccountResponse:
        async with transaction(self._session_factory) as session:
            row = await self._account(session, account_id, lock=True)
            await self._account_name_available(session, payload.name, exclude=account_id)
            row.name = payload.name
            row.enabled = payload.enabled
            row.updatedById = UUID(identity.user.id)
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_ACCOUNT_UPDATED", row.id)
            return self._account_response(row, await self._model_count(session, row.id))

    async def rotate_key(
        self,
        account_id: UUID,
        payload: RotateProviderAccountKeyRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ProviderAccountResponse:
        async with transaction(self._session_factory) as session:
            row = await self._account(session, account_id, lock=True)
            encrypted = self._cipher.encrypt(payload.apiKey)
            row.encryptedApiKey = encrypted.envelope
            row.keyLast4 = payload.apiKey[-4:]
            row.health = AiProviderAccountHealth.UNTESTED
            row.lastHealthAt = None
            row.lastHealthErrorCode = None
            row.updatedById = UUID(identity.user.id)
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_KEY_ROTATED", row.id)
            return self._account_response(row, await self._model_count(session, row.id))

    async def set_account_status(
        self,
        account_id: UUID,
        payload: ProviderAccountStatusRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ProviderAccountResponse:
        async with transaction(self._session_factory) as session:
            row = await self._account(session, account_id, lock=True)
            row.enabled = payload.enabled
            row.updatedById = UUID(identity.user.id)
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_ACCOUNT_STATUS", row.id)
            return self._account_response(row, await self._model_count(session, row.id))

    async def delete_account(
        self, account_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> None:
        async with transaction(self._session_factory) as session:
            row = await self._account(session, account_id, lock=True)
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_ACCOUNT_DELETED", row.id)
            await session.delete(row)

    async def list_models(self, account_id: UUID) -> list[ModelConfigResponse]:
        async with self._session_factory() as session:
            await self._account(session, account_id)
            rows = (
                await session.scalars(
                    select(AiModelConfig)
                    .where(AiModelConfig.accountId == account_id)
                    .order_by(
                        AiModelConfig.isDefault.desc(),
                        AiModelConfig.priority.asc(),
                        AiModelConfig.id.asc(),
                    )
                )
            ).all()
            return [self._model_response(row) for row in rows]

    async def create_model(
        self,
        account_id: UUID,
        payload: CreateModelConfigRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ModelConfigResponse:
        async with transaction(self._session_factory) as session:
            await self._account(session, account_id, lock=True)
            await self._model_name_available(session, account_id, payload.modelName)
            if payload.isDefault and payload.enabled:
                await self._clear_default(session, account_id)
            row = AiModelConfig(
                accountId=account_id,
                modelName=payload.modelName,
                enabled=payload.enabled,
                isDefault=payload.isDefault and payload.enabled,
                priority=payload.priority,
                timeoutSeconds=payload.timeoutSeconds,
            )
            session.add(row)
            await session.flush()
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_MODEL_CREATED", row.id)
            return self._model_response(row)

    async def update_model(
        self,
        account_id: UUID,
        model_id: UUID,
        payload: UpdateModelConfigRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ModelConfigResponse:
        async with transaction(self._session_factory) as session:
            row = await self._model(session, account_id, model_id, lock=True)
            await self._model_name_available(
                session, account_id, payload.modelName, exclude=model_id
            )
            if payload.isDefault and payload.enabled:
                await self._clear_default(session, account_id, exclude=model_id)
            changed = (
                row.modelName != payload.modelName
                or row.timeoutSeconds != payload.timeoutSeconds
            )
            row.modelName = payload.modelName
            row.enabled = payload.enabled
            row.isDefault = payload.isDefault and payload.enabled
            row.priority = payload.priority
            row.timeoutSeconds = payload.timeoutSeconds
            if changed:
                row.health = AiModelHealth.UNTESTED
                row.lastHealthAt = None
                row.lastHealthErrorCode = None
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_MODEL_UPDATED", row.id)
            return self._model_response(row)

    async def set_model_status(
        self,
        account_id: UUID,
        model_id: UUID,
        payload: ModelConfigStatusRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ModelConfigResponse:
        async with transaction(self._session_factory) as session:
            row = await self._model(session, account_id, model_id, lock=True)
            row.enabled = payload.enabled
            if not payload.enabled:
                row.isDefault = False
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_MODEL_STATUS", row.id)
            return self._model_response(row)

    async def set_default_model(
        self,
        account_id: UUID,
        model_id: UUID,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ModelConfigResponse:
        async with transaction(self._session_factory) as session:
            row = await self._model(session, account_id, model_id, lock=True)
            if not row.enabled:
                raise ApiError(400, "BAD_REQUEST", "停用模型不能设为默认模型")
            await self._clear_default(session, account_id, exclude=model_id)
            row.isDefault = True
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_MODEL_DEFAULT", row.id)
            return self._model_response(row)

    async def delete_model(
        self,
        account_id: UUID,
        model_id: UUID,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> None:
        async with transaction(self._session_factory) as session:
            row = await self._model(session, account_id, model_id, lock=True)
            await self._audit(session, identity, trace_id, "AI_PROVIDER_V2_MODEL_DELETED", row.id)
            await session.delete(row)

    async def discover_models(
        self,
        account_id: UUID,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> list[DiscoveredModelResponse]:
        async with self._session_factory() as session:
            account = await self._account(session, account_id)
            envelope = account.encryptedApiKey
            account_name = account.name
        started = monotonic()
        try:
            models = await self._adapter.list_models(envelope, 60)
        except ProviderError as error:
            await self._record_account_call(account_id, account_name, started, error=error)
            await self._update_account_test_health(account_id, error)
            await self._audit_separate(
                identity, trace_id, "AI_PROVIDER_V2_MODELS_DISCOVERY_FAILED", account_id
            )
            raise self._api_provider_error(error) from None
        await self._record_account_call(account_id, account_name, started)
        await self._mark_discovered_models(account_id, {model.id for model in models})
        await self._audit_separate(
            identity, trace_id, "AI_PROVIDER_V2_MODELS_DISCOVERED", account_id
        )
        return [DiscoveredModelResponse(id=model.id) for model in models]

    async def test_account(
        self, account_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> ProviderV2ConnectionResult:
        started = monotonic()
        try:
            models = await self.discover_models(account_id, identity, trace_id)
        except ApiError as error:
            if error.status_code != 502:
                raise
            return ProviderV2ConnectionResult(
                accountId=str(account_id),
                modelConfigId=None,
                success=False,
                latencyMs=max(0, round((monotonic() - started) * 1000)),
                errorClassification=error.code,
                availableModels=[],
            )
        return ProviderV2ConnectionResult(
            accountId=str(account_id),
            modelConfigId=None,
            success=True,
            latencyMs=max(0, round((monotonic() - started) * 1000)),
            errorClassification=None,
            availableModels=models,
        )

    async def test_model(
        self,
        account_id: UUID,
        model_id: UUID,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ProviderV2ConnectionResult:
        async with self._session_factory() as session:
            account = await self._account(session, account_id)
            model = await self._model(session, account_id, model_id)
            candidate = ProviderCandidate(
                account.id,
                account.name,
                ProviderType.DEEPSEEK_OFFICIAL,
                model.id,
                model.modelName,
                model.timeoutSeconds,
                account.encryptedApiKey,
            )
        started = monotonic()
        try:
            response = await self._adapter.chat(
                candidate,
                ProviderChatRequest((ProviderMessage(ProviderRole.USER, "ping"),)),
            )
        except ProviderError as error:
            runtime = ProviderV2Runtime(self._session_factory, self._adapter)
            await runtime._record_failure(candidate, error, started)
            await runtime._record_health_error(candidate, error)
            await self._audit_separate(
                identity, trace_id, "AI_PROVIDER_V2_MODEL_TESTED", model_id
            )
            return ProviderV2ConnectionResult(
                accountId=str(account_id),
                modelConfigId=str(model_id),
                success=False,
                latencyMs=max(0, round((monotonic() - started) * 1000)),
                errorClassification=error.classification.value,
                availableModels=[],
            )
        await ProviderV2Runtime(self._session_factory, self._adapter)._record_success(
            candidate, response
        )
        await self._audit_separate(identity, trace_id, "AI_PROVIDER_V2_MODEL_TESTED", model_id)
        return ProviderV2ConnectionResult(
            accountId=str(account_id),
            modelConfigId=str(model_id),
            success=True,
            latencyMs=response.latency_ms,
            errorClassification=None,
            availableModels=[],
        )

    async def _mark_discovered_models(self, account_id: UUID, discovered: set[str]) -> None:
        async with transaction(self._session_factory) as session:
            account = await self._account(session, account_id, lock=True)
            now = datetime.now(UTC)
            account.health = AiProviderAccountHealth.AVAILABLE
            account.lastHealthAt = now
            account.lastHealthErrorCode = None
            rows = (
                await session.scalars(
                    select(AiModelConfig)
                    .where(AiModelConfig.accountId == account_id)
                    .with_for_update()
                )
            ).all()
            for row in rows:
                row.lastHealthAt = now
                if row.modelName in discovered:
                    row.health = AiModelHealth.AVAILABLE
                    row.lastHealthErrorCode = None
                else:
                    row.health = AiModelHealth.CONFIG_ERROR
                    row.lastHealthErrorCode = ProviderErrorClassification.MODEL_NOT_FOUND.value

    async def _update_account_test_health(
        self, account_id: UUID, error: ProviderError
    ) -> None:
        if error.classification not in {
            ProviderErrorClassification.AUTHENTICATION,
            ProviderErrorClassification.PERMISSION,
            ProviderErrorClassification.CREDENTIAL_UNAVAILABLE,
        }:
            return
        async with transaction(self._session_factory) as session:
            row = await self._account(session, account_id, lock=True)
            row.health = AiProviderAccountHealth.CREDENTIAL_ERROR
            row.lastHealthAt = datetime.now(UTC)
            row.lastHealthErrorCode = error.classification.value

    async def _record_account_call(
        self,
        account_id: UUID,
        account_name: str,
        started: float,
        *,
        error: ProviderError | None = None,
    ) -> None:
        async with transaction(self._session_factory) as session:
            session.add(
                AiProviderV2CallLog(
                    accountId=account_id,
                    modelConfigId=None,
                    accountNameSnapshot=account_name,
                    modelNameSnapshot="/models",
                    httpStatus=error.status_code if error else 200,
                    durationMs=max(0, round((monotonic() - started) * 1000)),
                    result=AiCallResult.FAILURE if error else AiCallResult.SUCCESS,
                    errorClassification=error.classification.value if error else None,
                )
            )

    async def _account(
        self, session: AsyncSession, account_id: UUID, *, lock: bool = False
    ) -> AiProviderAccount:
        statement = select(AiProviderAccount).where(AiProviderAccount.id == account_id)
        if lock:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "AI Provider Account 不存在")
        return row

    async def _model(
        self,
        session: AsyncSession,
        account_id: UUID,
        model_id: UUID,
        *,
        lock: bool = False,
    ) -> AiModelConfig:
        statement = select(AiModelConfig).where(
            AiModelConfig.id == model_id, AiModelConfig.accountId == account_id
        )
        if lock:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "AI Model Config 不存在")
        return row

    async def _account_name_available(
        self, session: AsyncSession, name: str, *, exclude: UUID | None = None
    ) -> None:
        statement = select(AiProviderAccount.id).where(AiProviderAccount.name == name)
        if exclude is not None:
            statement = statement.where(AiProviderAccount.id != exclude)
        if await session.scalar(statement):
            raise ApiError(409, "CONFLICT", "Provider Account 名称已存在")

    async def _model_name_available(
        self,
        session: AsyncSession,
        account_id: UUID,
        model_name: str,
        *,
        exclude: UUID | None = None,
    ) -> None:
        statement = select(AiModelConfig.id).where(
            AiModelConfig.accountId == account_id, AiModelConfig.modelName == model_name
        )
        if exclude is not None:
            statement = statement.where(AiModelConfig.id != exclude)
        if await session.scalar(statement):
            raise ApiError(409, "CONFLICT", "Model Config 已存在")

    async def _clear_default(
        self, session: AsyncSession, account_id: UUID, *, exclude: UUID | None = None
    ) -> None:
        statement = (
            select(AiModelConfig)
            .where(AiModelConfig.accountId == account_id, AiModelConfig.isDefault.is_(True))
            .with_for_update()
        )
        if exclude is not None:
            statement = statement.where(AiModelConfig.id != exclude)
        for row in (await session.scalars(statement)).all():
            row.isDefault = False
        # PostgreSQL partial uniqueness is immediate. Flush the old default first so
        # setting the new row cannot depend on SQLAlchemy's UPDATE ordering.
        await session.flush()

    async def _model_counts(self, session: AsyncSession) -> dict[UUID, int]:
        rows = (
            await session.execute(
                select(AiModelConfig.accountId, func.count())
                .group_by(AiModelConfig.accountId)
            )
        ).all()
        return {account_id: int(count) for account_id, count in rows}

    async def _model_count(self, session: AsyncSession, account_id: UUID) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AiModelConfig)
                .where(AiModelConfig.accountId == account_id)
            )
            or 0
        )

    async def _audit(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        resource_id: UUID,
    ) -> None:
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="ADMIN_AI",
            action=action,
            resource_type="AI_PROVIDER_V2",
            resource_id=str(resource_id),
            trace_id=trace_id,
        )

    async def _audit_separate(
        self,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        resource_id: UUID,
    ) -> None:
        async with transaction(self._session_factory) as session:
            await self._audit(session, identity, trace_id, action, resource_id)

    @staticmethod
    def _account_response(row: AiProviderAccount, model_count: int) -> ProviderAccountResponse:
        return ProviderAccountResponse(
            id=str(row.id),
            name=row.name,
            providerType="DEEPSEEK_OFFICIAL",
            maskedKey=f"••••••••••••{row.keyLast4}",
            enabled=row.enabled,
            health=row.health.value,
            lastHealthAt=row.lastHealthAt.isoformat() if row.lastHealthAt else None,
            lastHealthErrorCode=row.lastHealthErrorCode,
            modelCount=model_count,
            createdAt=row.createdAt.isoformat(),
            updatedAt=row.updatedAt.isoformat(),
        )

    @staticmethod
    def _model_response(row: AiModelConfig) -> ModelConfigResponse:
        return ModelConfigResponse(
            id=str(row.id),
            accountId=str(row.accountId),
            modelName=row.modelName,
            enabled=row.enabled,
            isDefault=row.isDefault,
            priority=row.priority,
            timeoutSeconds=row.timeoutSeconds,
            health=row.health.value,
            lastHealthAt=row.lastHealthAt.isoformat() if row.lastHealthAt else None,
            lastHealthErrorCode=row.lastHealthErrorCode,
            createdAt=row.createdAt.isoformat(),
            updatedAt=row.updatedAt.isoformat(),
        )

    @staticmethod
    def _api_provider_error(error: ProviderError) -> ApiError:
        return ApiError(502, error.classification.value, "DeepSeek Official 调用失败")


__all__ = ["AiProviderV2Service", "ProviderV2Runtime"]
