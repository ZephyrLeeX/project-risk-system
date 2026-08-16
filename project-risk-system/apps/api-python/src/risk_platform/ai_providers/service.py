"""AI provider administration, selection and metadata-only call logging."""

from __future__ import annotations

import builtins
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.ai_providers.client import AiProviderClient
from risk_platform.ai_providers.models import (
    AiCallLog,
    AiCallResult,
    AiCallScene,
    AiConnectionStatus,
    AiProviderConfig,
    AiProviderProtocol,
)
from risk_platform.ai_providers.schemas import (
    CallDetail,
    CallResponse,
    CallsQuery,
    ConnectionResult,
    CreateProviderRequest,
    DraftTestRequest,
    PageResponse,
    ProviderQuery,
    ProviderResponse,
    ProviderStatusRequest,
    ProviderStrategy,
    ProviderSummary,
    RotateKeyRequest,
    UpdateProviderRequest,
    UsageOverview,
    UsageQuery,
    UsageTrend,
)
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.shared.crypto import SecretCipher, SecretCryptoError
from risk_platform.shared.errors import ApiError
from risk_platform.shared.outbound import OutboundEndpointGuard

NOTICE = "AI调用日志仅保留调用元数据，不包含完整密钥、提示词、模型原文或业务正文。"  # noqa: RUF001


class AiProvidersService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        client: AiProviderClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._client = client or AiProviderClient(OutboundEndpointGuard())

    async def summary(self) -> ProviderSummary:
        async with self._session_factory() as session:
            since = datetime.now(UTC) - timedelta(days=7)
            total = int(
                await session.scalar(select(func.count()).select_from(AiProviderConfig)) or 0
            )
            healthy = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AiProviderConfig)
                    .where(
                        AiProviderConfig.enabled.is_(True),
                        AiProviderConfig.lastTestStatus == AiConnectionStatus.HEALTHY,
                    )
                )
                or 0
            )
            expiring = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AiProviderConfig)
                    .where(
                        AiProviderConfig.enabled.is_(True),
                        AiProviderConfig.expiresAt >= date.today(),
                        AiProviderConfig.expiresAt <= date.today() + timedelta(days=30),
                    )
                )
                or 0
            )
            calls = (
                await session.scalars(select(AiCallLog).where(AiCallLog.createdAt >= since))
            ).all()
            success = sum(item.result == AiCallResult.SUCCESS for item in calls)
            return ProviderSummary(
                total=total,
                healthy=healthy,
                expiring=expiring,
                sevenDayCallTotal=len(calls),
                sevenDaySuccessRate=self._round(success / len(calls) * 100 if calls else 0),
            )

    async def list(self, query: ProviderQuery) -> list[ProviderResponse]:
        async with self._session_factory() as session:
            statement = select(AiProviderConfig).order_by(
                AiProviderConfig.isDefault.desc(),
                AiProviderConfig.priority.asc(),
                AiProviderConfig.createdAt.asc(),
            )
            if query.status == "ACTIVE":
                statement = statement.where(AiProviderConfig.enabled.is_(True))
            if query.status == "DISABLED":
                statement = statement.where(AiProviderConfig.enabled.is_(False))
            if query.keyword and (keyword := query.keyword.strip()):
                pattern = f"%{keyword}%"
                statement = statement.where(
                    or_(
                        AiProviderConfig.name.ilike(pattern),
                        AiProviderConfig.vendor.ilike(pattern),
                        AiProviderConfig.model.ilike(pattern),
                        AiProviderConfig.endpoint.ilike(pattern),
                    )
                )
            rows = (await session.scalars(statement)).all()
            usage = await self._usage_by_provider(session)
            return [self._provider(row, usage.get(row.id, 0)) for row in rows]

    async def strategy(self) -> builtins.list[ProviderStrategy]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(AiProviderConfig).order_by(
                        AiProviderConfig.isDefault.desc(),
                        AiProviderConfig.priority.asc(),
                        AiProviderConfig.createdAt.asc(),
                    )
                )
            ).all()
            return [
                ProviderStrategy(
                    id=str(row.id),
                    name=row.name,
                    enabled=row.enabled,
                    isDefault=row.isDefault,
                    priority=row.priority,
                )
                for row in rows
            ]

    async def create(
        self, payload: CreateProviderRequest, identity: SessionIdentity, trace_id: UUID
    ) -> ProviderResponse:
        async with transaction(self._session_factory) as session:
            await self._name_available(session, payload.name)
            self._endpoint_shape(payload.endpoint)
            encrypted = self._cipher.encrypt(payload.apiKey)
            count = int(
                await session.scalar(select(func.count()).select_from(AiProviderConfig)) or 0
            )
            row = AiProviderConfig(
                name=payload.name.strip(),
                vendor=payload.vendor.strip(),
                endpoint=self._normalize_endpoint(payload.endpoint),
                protocol=AiProviderProtocol(payload.protocol),
                model=payload.model.strip(),
                encryptedApiKey=encrypted.envelope,
                keyIv="",
                keyAuthTag="",
                keyLast4=payload.apiKey[-4:],
                expiresAt=payload.expiresAt,
                timeoutSeconds=payload.timeoutSeconds,
                retryCount=payload.retryCount,
                enabled=payload.enabled,
                isDefault=count == 0 and payload.enabled,
                priority=(count + 1) * 100,
                createdById=UUID(identity.user.id),
                updatedById=UUID(identity.user.id),
            )
            session.add(row)
            await session.flush()
            await self._audit(session, identity, trace_id, "AI_PROVIDER_CREATED", row)
            return self._provider(row, 0)

    async def update(
        self,
        provider_id: UUID,
        payload: UpdateProviderRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ProviderResponse:
        async with transaction(self._session_factory) as session:
            row = await self._get(session, provider_id, lock=True)
            await self._name_available(session, payload.name, provider_id)
            self._endpoint_shape(payload.endpoint)
            if row.isDefault and not payload.enabled:
                raise ApiError(400, "BAD_REQUEST", "默认服务不能直接停用，请先切换默认服务")  # noqa: RUF001
            (
                row.name,
                row.vendor,
                row.endpoint,
                row.protocol,
                row.model,
                row.expiresAt,
                row.timeoutSeconds,
                row.retryCount,
                row.enabled,
                row.updatedById,
            ) = (
                payload.name.strip(),
                payload.vendor.strip(),
                self._normalize_endpoint(payload.endpoint),
                AiProviderProtocol(payload.protocol),
                payload.model.strip(),
                payload.expiresAt,
                payload.timeoutSeconds,
                payload.retryCount,
                payload.enabled,
                UUID(identity.user.id),
            )
            await self._audit(session, identity, trace_id, "AI_PROVIDER_UPDATED", row)
            return self._provider(row, await self._usage_count(session, row.id))

    async def rotate_key(
        self,
        provider_id: UUID,
        payload: RotateKeyRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ProviderResponse:
        async with transaction(self._session_factory) as session:
            row = await self._get(session, provider_id, lock=True)
            encrypted = self._cipher.encrypt(payload.apiKey)
            (
                row.encryptedApiKey,
                row.keyLast4,
                row.keyIv,
                row.keyAuthTag,
                row.expiresAt,
                row.lastTestStatus,
                row.lastTestAt,
                row.lastTestLatencyMs,
                row.lastTestErrorCode,
                row.updatedById,
            ) = (
                encrypted.envelope,
                payload.apiKey[-4:],
                "",
                "",
                payload.expiresAt,
                AiConnectionStatus.UNTESTED,
                None,
                None,
                None,
                UUID(identity.user.id),
            )
            await self._audit(session, identity, trace_id, "AI_PROVIDER_KEY_ROTATED", row)
            return self._provider(row, await self._usage_count(session, row.id))

    async def set_status(
        self,
        provider_id: UUID,
        payload: ProviderStatusRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ProviderResponse:
        async with transaction(self._session_factory) as session:
            row = await self._get(session, provider_id, lock=True)
            if row.isDefault and not payload.enabled:
                raise ApiError(400, "BAD_REQUEST", "默认服务不能直接停用，请先切换默认服务")  # noqa: RUF001
            row.enabled, row.updatedById = payload.enabled, UUID(identity.user.id)
            await self._audit(session, identity, trace_id, "AI_PROVIDER_STATUS_CHANGED", row)
            return self._provider(row, await self._usage_count(session, row.id))

    async def set_default(
        self, provider_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> ProviderResponse:
        async with transaction(self._session_factory) as session:
            row = await self._get(session, provider_id, lock=True)
            if not row.enabled:
                raise ApiError(400, "BAD_REQUEST", "停用的AI服务不能设为默认服务")
            for current in (
                await session.scalars(
                    select(AiProviderConfig)
                    .where(AiProviderConfig.isDefault.is_(True))
                    .with_for_update()
                )
            ).all():
                current.isDefault = False
                current.updatedById = UUID(identity.user.id)
            row.isDefault, row.updatedById = True, UUID(identity.user.id)
            await self._audit(session, identity, trace_id, "AI_PROVIDER_DEFAULT_CHANGED", row)
            return self._provider(row, await self._usage_count(session, row.id))

    async def test_provider(
        self, provider_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> ConnectionResult:
        async with self._session_factory() as session:
            row = await self._get(session, provider_id)
            try:
                key = self._cipher.decrypt(row.encryptedApiKey)
            except SecretCryptoError as error:
                raise ApiError(500, "AI_CREDENTIAL_UNAVAILABLE", "AI服务凭据不可用") from error
            return await self._run_test(
                row.id,
                row.name,
                row.endpoint,
                row.protocol,
                row.model,
                key,
                row.timeoutSeconds,
                row.retryCount,
                identity,
                trace_id,
            )

    async def test_draft(
        self, payload: DraftTestRequest, identity: SessionIdentity, trace_id: UUID
    ) -> ConnectionResult:
        return await self._run_test(
            None,
            payload.name.strip(),
            payload.endpoint,
            AiProviderProtocol(payload.protocol),
            payload.model.strip(),
            payload.apiKey,
            payload.timeoutSeconds,
            payload.retryCount,
            identity,
            trace_id,
        )

    async def test_all(
        self, identity: SessionIdentity, trace_id: UUID
    ) -> builtins.list[ConnectionResult]:
        async with self._session_factory() as session:
            ids = list(
                (
                    await session.scalars(
                        select(AiProviderConfig.id).where(AiProviderConfig.enabled.is_(True))
                    )
                ).all()
            )
        return [await self.test_provider(provider_id, identity, trace_id) for provider_id in ids]

    async def usage(self, query: UsageQuery) -> UsageOverview:
        async with self._session_factory() as session:
            end, start = datetime.now(UTC), datetime.now(UTC) - timedelta(days=7)
            statement = (
                select(AiCallLog)
                .where(AiCallLog.createdAt >= start, AiCallLog.createdAt <= end)
                .order_by(AiCallLog.createdAt.asc())
            )
            if query.scene:
                statement = statement.where(AiCallLog.scene == query.scene)
            rows = (await session.scalars(statement)).all()
            durations = sorted(row.durationMs for row in rows)
            success = sum(row.result == AiCallResult.SUCCESS for row in rows)
            trend = {(start.date() + timedelta(days=i)).isoformat(): 0 for i in range(7)}
            for row in rows:
                trend[row.createdAt.date().isoformat()] = (
                    trend.get(row.createdAt.date().isoformat(), 0) + 1
                )
            return UsageOverview(
                rangeStart=start.isoformat(),
                rangeEnd=end.isoformat(),
                callTotal=len(rows),
                successTotal=success,
                successRate=self._round(success / len(rows) * 100 if rows else 0),
                averageDurationMs=round(sum(durations) / len(durations)) if durations else 0,
                p95DurationMs=durations[
                    min(len(durations) - 1, max(0, int(len(durations) * 0.95) - 1))
                ]
                if durations
                else 0,
                totalTokens=sum(row.totalTokens for row in rows),
                trend=[UsageTrend(date=key, count=value) for key, value in trend.items()],
            )

    async def calls(self, query: CallsQuery) -> PageResponse:
        async with self._session_factory() as session:
            statement = select(AiCallLog).order_by(AiCallLog.createdAt.desc())
            if query.result:
                statement = statement.where(AiCallLog.result == query.result)
            if query.scene:
                statement = statement.where(AiCallLog.scene == query.scene)
            rows = (
                await session.scalars(
                    statement.offset((query.page - 1) * query.pageSize).limit(query.pageSize)
                )
            ).all()
            count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AiCallLog)
                    .where(
                        *[
                            condition
                            for condition in (
                                AiCallLog.result == query.result if query.result else None,
                                AiCallLog.scene == query.scene if query.scene else None,
                            )
                            if condition
                        ]
                    )
                )
                or 0
            )
            return PageResponse(
                items=[self._call(row) for row in rows],
                page=query.page,
                pageSize=query.pageSize,
                total=count,
            )

    async def call_detail(self, call_id: UUID) -> CallDetail:
        async with self._session_factory() as session:
            row = await session.scalar(select(AiCallLog).where(AiCallLog.id == call_id))
            if row is None:
                raise ApiError(404, "NOT_FOUND", "AI调用记录不存在")
            actor = await session.scalar(select(User.displayName).where(User.id == row.actorUserId))
            return CallDetail(
                **self._call(row).model_dump(),
                inputTokens=row.inputTokens,
                outputTokens=row.outputTokens,
                actorDisplayName=actor,
                dataProtectionNotice=NOTICE,
            )

    async def _run_test(
        self,
        provider_id: UUID | None,
        name: str,
        endpoint: str,
        protocol: AiProviderProtocol,
        model: str,
        key: str,
        timeout: int,
        retries: int,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ConnectionResult:
        outcome = await self._client.test(endpoint, model, key, timeout, retries, protocol)
        tested = datetime.now(UTC)
        test_trace = uuid4()
        async with transaction(self._session_factory) as session:
            if provider_id:
                row = await self._get(session, provider_id, lock=True)
                (
                    row.lastTestStatus,
                    row.lastTestAt,
                    row.lastTestLatencyMs,
                    row.lastTestErrorCode,
                    row.updatedById,
                ) = (
                    AiConnectionStatus.HEALTHY if outcome.success else AiConnectionStatus.FAILED,
                    tested,
                    outcome.latency_ms,
                    outcome.error_code,
                    UUID(identity.user.id),
                )
            session.add(
                AiCallLog(
                    traceId=str(test_trace),
                    providerId=provider_id,
                    providerNameSnapshot=name,
                    modelSnapshot=model,
                    scene=AiCallScene.CONNECTION_TEST,
                    durationMs=outcome.latency_ms,
                    result=AiCallResult.SUCCESS if outcome.success else AiCallResult.FAILURE,
                    errorCode=outcome.error_code,
                    errorSummary=outcome.error_summary,
                    actorUserId=UUID(identity.user.id),
                )
            )
            await AuditService(session).record_success(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="ADMIN_AI",
                action="AI_PROVIDER_TESTED" if provider_id else "AI_PROVIDER_DRAFT_TESTED",
                resource_type="AI_PROVIDER",
                resource_id=str(provider_id) if provider_id else None,
                trace_id=test_trace,
            )
        return ConnectionResult(
            providerId=str(provider_id) if provider_id else None,
            providerName=name,
            model=model,
            success=outcome.success,
            latencyMs=outcome.latency_ms,
            errorCode=outcome.error_code,
            errorSummary=outcome.error_summary,
            testedAt=tested.isoformat(),
            traceId=str(test_trace),
        )

    async def _get(
        self, session: AsyncSession, provider_id: UUID, lock: bool = False
    ) -> AiProviderConfig:
        statement = select(AiProviderConfig).where(AiProviderConfig.id == provider_id)
        if lock:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "AI服务配置不存在")
        return row

    async def _name_available(
        self, session: AsyncSession, name: str, exclude: UUID | None = None
    ) -> None:
        statement = select(AiProviderConfig.id).where(AiProviderConfig.name == name.strip())
        if exclude:
            statement = statement.where(AiProviderConfig.id != exclude)
        if await session.scalar(statement):
            raise ApiError(409, "CONFLICT", "AI服务配置名称已存在")

    async def _audit(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        row: AiProviderConfig,
    ) -> None:
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="ADMIN_AI",
            action=action,
            resource_type="AI_PROVIDER",
            resource_id=str(row.id),
            trace_id=trace_id,
        )

    async def _usage_by_provider(self, session: AsyncSession) -> dict[UUID, int]:
        rows = (
            await session.execute(
                select(AiCallLog.providerId, func.count())
                .where(
                    AiCallLog.createdAt >= datetime.now(UTC) - timedelta(days=7),
                    AiCallLog.providerId.is_not(None),
                )
                .group_by(AiCallLog.providerId)
            )
        ).all()
        return {provider_id: int(count) for provider_id, count in rows if provider_id is not None}

    async def _usage_count(self, session: AsyncSession, provider_id: UUID) -> int:
        return (await self._usage_by_provider(session)).get(provider_id, 0)

    @staticmethod
    def _provider(row: AiProviderConfig, usage: int) -> ProviderResponse:
        return ProviderResponse(
            id=str(row.id),
            name=row.name,
            vendor=row.vendor,
            endpoint=row.endpoint,
            protocol=row.protocol.value,
            model=row.model,
            maskedKey=f"••••••••••••{row.keyLast4}",
            expiresAt=row.expiresAt.isoformat() if row.expiresAt else None,
            timeoutSeconds=row.timeoutSeconds,
            retryCount=row.retryCount,
            enabled=row.enabled,
            isDefault=row.isDefault,
            priority=row.priority,
            lastTestStatus=row.lastTestStatus.value,
            lastTestAt=row.lastTestAt.isoformat() if row.lastTestAt else None,
            lastTestLatencyMs=row.lastTestLatencyMs,
            lastTestErrorCode=row.lastTestErrorCode,
            sevenDayUsageCount=usage,
            createdAt=row.createdAt.isoformat(),
            updatedAt=row.updatedAt.isoformat(),
        )

    @staticmethod
    def _call(row: AiCallLog) -> CallResponse:
        return CallResponse(
            id=str(row.id),
            traceId=row.traceId,
            providerName=row.providerNameSnapshot,
            model=row.modelSnapshot,
            scene=row.scene.value,
            totalTokens=row.totalTokens,
            durationMs=row.durationMs,
            result=row.result.value,
            errorCode=row.errorCode,
            errorSummary=row.errorSummary,
            createdAt=row.createdAt.isoformat(),
        )

    @staticmethod
    def _round(value: float) -> float:
        return round(value, 1)

    @staticmethod
    def _normalize_endpoint(value: str) -> str:
        return value.strip().rstrip("/")

    @staticmethod
    def _endpoint_shape(value: str) -> None:
        if not value.strip().lower().startswith("https://"):
            raise ApiError(400, "BAD_REQUEST", "AI服务地址必须使用HTTPS")


__all__ = ["AiProvidersService"]
