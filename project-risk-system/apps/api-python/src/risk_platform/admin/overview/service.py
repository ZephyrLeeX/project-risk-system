"""Bounded, safe aggregation for the management overview."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Literal, Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.admin.overview.schemas import (
    AdminOverview,
    AttentionItem,
    HealthItem,
    OverviewLink,
    RecentAuditItem,
    UnavailableSection,
)
from risk_platform.ai_providers.client import AiProviderClient, ConnectionOutcome
from risk_platform.ai_providers.models import AiConnectionStatus, AiProviderConfig
from risk_platform.audit.models import AuditLog
from risk_platform.audit.schemas import AuditModuleKey
from risk_platform.auth.service import SessionIdentity
from risk_platform.imports.models import ImportBatch, ImportBatchStatus
from risk_platform.shared.crypto import SecretCipher

Check = Callable[[], Awaitable[None]]
_CHECK_TIMEOUT_SECONDS = 2


class ProviderClient(Protocol):
    async def test(
        self, endpoint: str, model: str, api_key: str, timeout_seconds: int, retry_count: int
    ) -> ConnectionOutcome: ...


class OverviewDependencyFailure(RuntimeError):
    """A dependency check failed without exposing its details."""

    def __init__(self, code: Literal["TIMEOUT", "UNREACHABLE", "NO_ACTIVE_WORKER", "CHECK_FAILED"]):
        self.code = code


class AdminOverviewService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher | None,
        provider_client: ProviderClient | None = None,
        *,
        api_check: Check | None = None,
        database_check: Check | None = None,
        redis_check: Check | None = None,
        worker_check: Check | None = None,
    ) -> None:
        self._factory = session_factory
        self._cipher = cipher
        self._provider_client = provider_client or AiProviderClient()
        self._api_check = api_check or _unconfigured_api_check
        self._database_check = database_check or self._database_liveness
        if redis_check is None or worker_check is None:
            from risk_platform.admin.overview.health_checks import redis_ping, worker_ping

            self._redis_check = redis_check or redis_ping
            self._worker_check = worker_check or worker_ping
        else:
            self._redis_check = redis_check
            self._worker_check = worker_check

    async def overview(self, identity: SessionIdentity) -> AdminOverview:
        unavailable: list[UnavailableSection] = []
        health = await self._section(
            "health",
            "admin.config.manage" in identity.user.permissions,
            self._health,
            unavailable,
            bounded=False,
        )
        attention = await self._section(
            "attention",
            "risk.manage_all" in identity.user.permissions,
            self._attention,
            unavailable,
        )
        recent = await self._section(
            "recentAudit",
            "admin.audit.view" in identity.user.permissions,
            self._recent_audit,
            unavailable,
        )
        return AdminOverview(
            generatedAt=_utc_milliseconds(),
            health=health,
            attention=attention,
            recentAudit=recent,
            unavailableSections=unavailable,
        )

    async def _section[T](
        self,
        section: Literal["health", "attention", "recentAudit"],
        allowed: bool,
        query: Callable[[], Awaitable[T]],
        unavailable: list[UnavailableSection],
        *,
        bounded: bool = True,
    ) -> T | None:
        if not allowed:
            unavailable.append(
                UnavailableSection(section=section, reason="FORBIDDEN", code="FORBIDDEN")
            )
            return None
        try:
            if bounded:
                return await asyncio.wait_for(query(), timeout=_CHECK_TIMEOUT_SECONDS)
            return await query()
        except TimeoutError:
            unavailable.append(
                UnavailableSection(section=section, reason="TIMEOUT", code="TIMEOUT")
            )
        except Exception:
            unavailable.append(
                UnavailableSection(
                    section=section, reason="DEPENDENCY_FAILURE", code="DEPENDENCY_FAILURE"
                )
            )
        return None

    async def _health(self) -> list[HealthItem]:
        checks = await asyncio.gather(
            self._simple_health("API", "API 服务", "API 服务可用", self._api_check),
            self._simple_health("DATABASE", "数据库", "数据库连接正常", self._database_check),
            self._simple_health("REDIS", "Redis", "Redis 连接正常", self._redis_check),
            self._simple_health("WORKER", "Worker", "Worker 在线", self._worker_check),
            self._provider_health(),
        )
        return list(checks)

    async def _simple_health(
        self,
        key: Literal["API", "DATABASE", "REDIS", "WORKER"],
        label: str,
        success_summary: str,
        check: Check,
    ) -> HealthItem:
        checked = _utc_milliseconds()
        try:
            await asyncio.wait_for(check(), timeout=_CHECK_TIMEOUT_SECONDS)
            return HealthItem(
                key=key,
                label=label,
                status="HEALTHY",
                checkedAt=checked,
                summary=success_summary,
                code=None,
                link=None,
            )
        except TimeoutError:
            code: Literal["TIMEOUT", "UNREACHABLE", "NO_ACTIVE_WORKER", "CHECK_FAILED"] = "TIMEOUT"
        except OverviewDependencyFailure as error:
            code = error.code
        except Exception:
            code = "CHECK_FAILED"
        return HealthItem(
            key=key,
            label=label,
            status="UNAVAILABLE",
            checkedAt=checked,
            summary=_failure_summary(key, code),
            code=code,
            link=None,
        )

    async def _database_liveness(self) -> None:
        async with self._factory() as session:
            await session.execute(text("SELECT 1"))

    async def _provider_health(self) -> HealthItem:
        checked = _utc_milliseconds()
        async with self._factory() as session:
            providers = (
                await session.scalars(
                    select(AiProviderConfig).where(AiProviderConfig.enabled.is_(True))
                )
            ).all()
        if not providers:
            return _provider_item(
                checked, "UNAVAILABLE", "未配置已启用的 AI 服务", "NO_ENABLED_PROVIDER"
            )
        outcomes = await asyncio.gather(*(self._test_provider(row) for row in providers))
        successes = sum(outcome is None for outcome in outcomes)
        if successes == len(providers):
            return _provider_item(checked, "HEALTHY", "AI 服务连接正常", None)
        code: Literal["TIMEOUT", "CHECK_FAILED"] = (
            "TIMEOUT" if all(outcome == "TIMEOUT" for outcome in outcomes) else "CHECK_FAILED"
        )
        if successes:
            return _provider_item(checked, "DEGRADED", "部分 AI 服务连接异常", code)
        return _provider_item(checked, "UNAVAILABLE", "AI 服务连接不可用", code)

    async def _test_provider(
        self, provider: AiProviderConfig
    ) -> Literal["TIMEOUT", "CHECK_FAILED"] | None:
        if self._cipher is None:
            return "CHECK_FAILED"
        try:
            key = self._cipher.decrypt(provider.encryptedApiKey)
            result = await asyncio.wait_for(
                self._provider_client.test(provider.endpoint, provider.model, key, 2, 0),
                timeout=_CHECK_TIMEOUT_SECONDS,
            )
            if result.success:
                return None
            return "TIMEOUT" if result.error_code == "UPSTREAM_TIMEOUT" else "CHECK_FAILED"
        except TimeoutError:
            return "TIMEOUT"
        except Exception:
            return "CHECK_FAILED"

    async def _attention(self) -> list[AttentionItem]:
        now = datetime.now(UTC)
        async with self._factory() as session:
            batches = (
                await session.scalars(
                    select(ImportBatch).where(ImportBatch.status == ImportBatchStatus.PREVIEWED)
                )
            ).all()
            providers = (
                await session.scalars(
                    select(AiProviderConfig).where(AiProviderConfig.enabled.is_(True))
                )
            ).all()
        items: list[AttentionItem] = []
        for batch in batches:
            critical = any((batch.errorRows, batch.supplementalErrorRows, batch.legalErrorRows))
            items.append(
                AttentionItem(
                    id=f"IMPORT_REVIEW:{batch.id}",
                    kind="IMPORT_REVIEW",
                    status="CRITICAL" if critical else "WARNING",
                    title="导入批次需要复核",
                    summary="批次包含待确认的导入结果",
                    occurredAt=batch.createdAt,
                    link=OverviewLink(path="/admin/imports", query={"batchId": batch.id}),
                )
            )
        cutoff = now + timedelta(days=30)
        for provider in providers:
            link = OverviewLink(path="/admin/api-keys", query={"providerId": provider.id})
            if provider.lastTestStatus in {AiConnectionStatus.FAILED, AiConnectionStatus.UNTESTED}:
                failed = provider.lastTestStatus is AiConnectionStatus.FAILED
                items.append(
                    AttentionItem(
                        id=f"AI_PROVIDER_CONNECTION:{provider.id}",
                        kind="AI_PROVIDER_CONNECTION",
                        status="CRITICAL" if failed else "WARNING",
                        title="AI 服务连接需要复核",
                        summary="AI 服务最近连接状态需要处理",
                        occurredAt=provider.lastTestAt or provider.updatedAt,
                        link=link,
                    )
                )
            if (
                provider.expiresAt is not None
                and datetime.combine(provider.expiresAt, time.min, tzinfo=UTC) < cutoff
            ):
                expired = datetime.combine(provider.expiresAt, time.min, tzinfo=UTC) < now
                items.append(
                    AttentionItem(
                        id=f"AI_PROVIDER_EXPIRY:{provider.id}",
                        kind="AI_PROVIDER_EXPIRY",
                        status="CRITICAL" if expired else "WARNING",
                        title="AI 服务密钥即将到期",
                        summary="AI 服务凭据需要及时更新",
                        occurredAt=datetime.combine(provider.expiresAt, time.min, tzinfo=UTC),
                        link=link,
                    )
                )
        return sorted(
            items,
            key=lambda item: (item.status != "CRITICAL", -item.occurredAt.timestamp(), item.id),
        )

    async def _recent_audit(self) -> list[RecentAuditItem]:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(AuditLog, User.displayName)
                    .outerjoin(User, User.id == AuditLog.actorUserId)
                    .order_by(AuditLog.createdAt.desc(), AuditLog.id.desc())
                    .limit(10)
                )
            ).all()
        return [
            RecentAuditItem(
                id=entry.id,
                occurredAt=entry.createdAt,
                actorName=name or "系统任务",
                module=_audit_module(entry.module),
                action=entry.action,
                summary=_audit_summary(entry.action, entry.resourceType, entry.resourceId),
                result=entry.result.value,
                resourceType=entry.resourceType,
                resourceId=entry.resourceId,
                traceId=entry.traceId,
                link=OverviewLink(path="/admin/audit-logs", query={"auditId": entry.id}),
            )
            for entry, name in rows
        ]


async def _unconfigured_api_check() -> None:
    raise OverviewDependencyFailure("CHECK_FAILED")


def _failure_summary(key: str, code: str) -> str:
    labels = {"API": "API 服务", "DATABASE": "数据库", "REDIS": "Redis", "WORKER": "Worker"}
    suffix = "检查超时" if code == "TIMEOUT" else "当前不可用"
    return f"{labels[key]}{suffix}"


def _utc_milliseconds() -> datetime:
    value = datetime.now(UTC)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _provider_item(
    checked: datetime,
    status: Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"],
    summary: str,
    code: Literal["TIMEOUT", "CHECK_FAILED", "NO_ENABLED_PROVIDER"] | None,
) -> HealthItem:
    return HealthItem(
        key="AI_PROVIDER",
        label="AI 服务",
        status=status,
        checkedAt=checked,
        summary=summary,
        code=code,
        link=OverviewLink(path="/admin/api-keys", query={}) if code else None,
    )


def _audit_module(value: str) -> AuditModuleKey:
    modules = {
        AuditModuleKey.AUTH: {"AUTH"},
        AuditModuleKey.PERMISSION: {"ADMIN_USER", "ADMIN_ROLE"},
        AuditModuleKey.MAILBOX: {"MAILBOX", "MAIL_SYNC", "MAIL_MESSAGE"},
        AuditModuleKey.AI: {"AI", "ADMIN_AI"},
        AuditModuleKey.RISK: {"RISK", "TODO"},
        AuditModuleKey.IMPORT: {"IMPORT"},
        AuditModuleKey.CONFIG: {"SYSTEM_CONFIG"},
        AuditModuleKey.AUDIT: {"AUDIT"},
    }
    return next((key for key, values in modules.items() if value in values), AuditModuleKey.OTHER)


def _audit_summary(action: str, resource_type: str, resource_id: str | None) -> str:
    labels = {
        "CREATE": "创建",
        "UPDATE": "更新",
        "TEST": "测试",
        "LOGIN": "登录",
        "PUBLISH": "发布",
        "ROLLBACK": "回滚",
        "EXPORT": "导出",
    }
    label = next((label for key, label in labels.items() if key in action), action)
    return f"{label} · {resource_type}{('/' + resource_id) if resource_id else ''}"


__all__ = ["AdminOverviewService", "OverviewDependencyFailure"]
