"""Fail-closed retention decisions and serialized audit-hold state transitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.agent.models import AgentConversation
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.imports.models import ImportBatch
from risk_platform.retention.configuration import (
    DEFAULT_RETENTION_CONFIG_VERSION,
    FrozenRetentionConfiguration,
    RetentionSettings,
    is_utc,
    require_utc,
)
from risk_platform.retention.models import (
    RetentionHold,
    RetentionHoldReason,
    RetentionHoldStatus,
    RetentionResourceType,
)
from risk_platform.shared.errors import ApiError
from risk_platform.system_config.models import SystemConfigRelease


class RetentionDecision(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    MISSING_RETENTION_FACT = "MISSING_RETENTION_FACT"
    ACTIVE_AUDIT_HOLD = "ACTIVE_AUDIT_HOLD"
    ROLLBACK_WINDOW = "ROLLBACK_WINDOW"
    ACTIVE_OPERATION = "ACTIVE_OPERATION"
    RETENTION_NOT_DUE = "RETENTION_NOT_DUE"


@dataclass(frozen=True, slots=True)
class ProtectionResult:
    decision: RetentionDecision

    @property
    def eligible(self) -> bool:
        return self.decision is RetentionDecision.ELIGIBLE


@dataclass(frozen=True, slots=True)
class CreatedHold:
    hold: RetentionHold
    created: bool


class RetentionConfigurationRepository:
    """Reads a policy only to freeze new facts; cleanup never consults it."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self) -> FrozenRetentionConfiguration:
        row = await self._session.scalar(
            select(SystemConfigRelease)
            .order_by(SystemConfigRelease.publishedAt.desc(), SystemConfigRelease.id.desc())
            .limit(1)
        )
        if row is None:
            return FrozenRetentionConfiguration(
                version=DEFAULT_RETENTION_CONFIG_VERSION, settings=RetentionSettings()
            )
        settings = self._settings(row.snapshot)
        if settings is None:
            # Releases created before ADR 0027 are interpreted only with its approved defaults.
            settings = RetentionSettings()
        return FrozenRetentionConfiguration(version=row.version, settings=settings)

    async def for_version(self, version: str | None) -> FrozenRetentionConfiguration | None:
        if version == DEFAULT_RETENTION_CONFIG_VERSION:
            return FrozenRetentionConfiguration(version=version, settings=RetentionSettings())
        if not version:
            return None
        row = await self._session.scalar(
            select(SystemConfigRelease).where(SystemConfigRelease.version == version)
        )
        if row is None:
            return None
        # Releases predating ADR 0027 are valid frozen defaults, including when
        # later import confirmation consults their recorded release version.
        return FrozenRetentionConfiguration(
            version=row.version,
            settings=self._settings(row.snapshot) or RetentionSettings(),
        )

    @staticmethod
    def _settings(snapshot: Any) -> RetentionSettings | None:
        if not isinstance(snapshot, dict):
            return None
        value = snapshot.get("retention")
        try:
            return RetentionSettings.model_validate(value) if value is not None else None
        except ValueError:
            return None


class RetentionProtectionService:
    """Re-read locked facts and return ADR 0027's closed cleanup result."""

    @staticmethod
    def import_batch(
        batch: ImportBatch | None,
        *,
        retention_config_known: bool = False,
        has_active_hold: bool,
        active_operation: bool,
        as_of: datetime,
    ) -> ProtectionResult:
        require_utc(as_of, field="as_of")
        if (
            batch is None
            or not retention_config_known
            or not batch.retentionConfigVersion
            or not is_utc(batch.sourceExpiresAt)
            or not (batch.rollbackProtectedUntil is None or is_utc(batch.rollbackProtectedUntil))
        ):
            return ProtectionResult(RetentionDecision.MISSING_RETENTION_FACT)
        if has_active_hold:
            return ProtectionResult(RetentionDecision.ACTIVE_AUDIT_HOLD)
        if batch.rollbackProtectedUntil is not None and as_of < batch.rollbackProtectedUntil:
            return ProtectionResult(RetentionDecision.ROLLBACK_WINDOW)
        if active_operation:
            return ProtectionResult(RetentionDecision.ACTIVE_OPERATION)
        if as_of < batch.sourceExpiresAt:
            return ProtectionResult(RetentionDecision.RETENTION_NOT_DUE)
        return ProtectionResult(RetentionDecision.ELIGIBLE)

    @staticmethod
    def conversation(
        *,
        expires_at: datetime | None,
        retention_config_version: str | None,
        retention_config_known: bool = False,
        has_active_hold: bool,
        active_operation: bool,
        as_of: datetime,
    ) -> ProtectionResult:
        require_utc(as_of, field="as_of")
        if not retention_config_known or not retention_config_version or not is_utc(expires_at):
            return ProtectionResult(RetentionDecision.MISSING_RETENTION_FACT)
        assert expires_at is not None
        if has_active_hold:
            return ProtectionResult(RetentionDecision.ACTIVE_AUDIT_HOLD)
        if active_operation:
            return ProtectionResult(RetentionDecision.ACTIVE_OPERATION)
        if as_of < expires_at:
            return ProtectionResult(RetentionDecision.RETENTION_NOT_DUE)
        return ProtectionResult(RetentionDecision.ELIGIBLE)


class LockedRetentionProtectionService:
    """Recheck deletion eligibility under ADR 0027's mandatory lock sequence."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._holds = RetentionHoldService(session_factory)

    async def import_batch(
        self,
        *,
        batch_id: UUID,
        active_operation: bool,
        as_of: datetime,
        trace_id: UUID,
    ) -> ProtectionResult:
        require_utc(as_of, field="as_of")
        resource_id = str(batch_id)
        async with transaction(self._session_factory) as session:
            await self._holds._lock_resource(
                session, RetentionResourceType.IMPORT_BATCH, resource_id
            )
            await self._holds._lock_resource_fact(
                session, RetentionResourceType.IMPORT_BATCH, resource_id
            )
            batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
            )
            if batch is None:
                return ProtectionResult(RetentionDecision.MISSING_RETENTION_FACT)
            await self._holds.expire_due_locked(
                session,
                resource_type=RetentionResourceType.IMPORT_BATCH,
                resource_id=resource_id,
                as_of=as_of,
                trace_id=trace_id,
            )
            active_hold = await self._holds._active_hold(
                session, RetentionResourceType.IMPORT_BATCH, resource_id
            )
            known = await RetentionConfigurationRepository(session).for_version(
                batch.retentionConfigVersion
            )
            return RetentionProtectionService.import_batch(
                batch,
                retention_config_known=known is not None,
                has_active_hold=active_hold is not None,
                active_operation=active_operation,
                as_of=as_of,
            )

    async def conversation(
        self,
        *,
        conversation_id: UUID,
        active_operation: bool,
        as_of: datetime,
        trace_id: UUID,
    ) -> ProtectionResult:
        require_utc(as_of, field="as_of")
        resource_id = str(conversation_id)
        async with transaction(self._session_factory) as session:
            await self._holds._lock_resource(
                session, RetentionResourceType.AGENT_CONVERSATION, resource_id
            )
            await self._holds._lock_resource_fact(
                session, RetentionResourceType.AGENT_CONVERSATION, resource_id
            )
            conversation = await session.scalar(
                select(AgentConversation)
                .where(AgentConversation.id == conversation_id)
                .with_for_update()
            )
            if conversation is None:
                return ProtectionResult(RetentionDecision.MISSING_RETENTION_FACT)
            await self._holds.expire_due_locked(
                session,
                resource_type=RetentionResourceType.AGENT_CONVERSATION,
                resource_id=resource_id,
                as_of=as_of,
                trace_id=trace_id,
            )
            active_hold = await self._holds._active_hold(
                session, RetentionResourceType.AGENT_CONVERSATION, resource_id
            )
            known = await RetentionConfigurationRepository(session).for_version(
                conversation.retentionConfigVersion
            )
            return RetentionProtectionService.conversation(
                expires_at=conversation.expiresAt,
                retention_config_version=conversation.retentionConfigVersion,
                retention_config_known=known is not None,
                has_active_hold=active_hold is not None,
                active_operation=active_operation,
                as_of=as_of,
            )


class RetentionHoldService:
    """Human-only hold changes, with advisory locks for resources lacking a table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        resource_type: RetentionResourceType,
        resource_id: str,
        reason: RetentionHoldReason,
        expires_at: datetime | None,
        identity: SessionIdentity,
        trace_id: UUID,
        as_of: datetime,
    ) -> CreatedHold:
        try:
            self._require_human_admin(identity)
            require_utc(as_of, field="as_of")
            normalized_resource_id = self._validate_resource(resource_type, resource_id)
            if expires_at is not None:
                require_utc(expires_at, field="expires_at")
                if expires_at <= as_of:
                    raise ApiError(422, "VALIDATION_ERROR", "留存保全到期时间无效")
            async with transaction(self._session_factory) as session:
                await self._lock_resource(session, resource_type, normalized_resource_id)
                await self._lock_resource_fact(session, resource_type, normalized_resource_id)
                await self.expire_due_locked(
                    session,
                    resource_type=resource_type,
                    resource_id=normalized_resource_id,
                    as_of=as_of,
                    trace_id=trace_id,
                )
                active = await self._active_hold(session, resource_type, normalized_resource_id)
                if active is not None:
                    if active.reason is reason and active.expiresAt == expires_at:
                        return CreatedHold(hold=active, created=False)
                    raise ApiError(
                        409,
                        "RETENTION_HOLD_ALREADY_ACTIVE",
                        "该资源已有生效中的留存保全",
                    )
                row = RetentionHold(
                    resourceType=resource_type,
                    resourceId=normalized_resource_id,
                    reason=reason,
                    createdById=UUID(identity.user.id),
                    createdTraceId=str(trace_id),
                    createdAt=as_of,
                    expiresAt=expires_at,
                )
                session.add(row)
                await session.flush()
                await AuditService(session).record_success(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="RETENTION",
                    action="RETENTION_HOLD_CREATED",
                    resource_type="RETENTION_HOLD",
                    resource_id=str(row.id),
                    trace_id=trace_id,
                )
                return CreatedHold(hold=row, created=True)
        except Exception as error:
            await self._record_failure(
                identity,
                trace_id,
                resource_type.value,
                resource_id,
                self._failure_code(error),
            )
            raise

    async def release(
        self, *, hold_id: UUID, identity: SessionIdentity, trace_id: UUID, as_of: datetime
    ) -> RetentionHold:
        try:
            self._require_human_admin(identity)
            require_utc(as_of, field="as_of")
            async with self._session_factory() as locating_session:
                located = await locating_session.execute(
                    select(RetentionHold.resourceType, RetentionHold.resourceId).where(
                        RetentionHold.id == hold_id
                    )
                )
                resource = located.one_or_none()
            if resource is None:
                raise ApiError(404, "RETENTION_HOLD_NOT_FOUND", "留存保全不存在")
            resource_type, resource_id = resource._tuple()
            if resource_type is RetentionResourceType.BACKUP_COPY:
                raise ApiError(
                    409,
                    "RETENTION_BACKUP_COPY_UNAVAILABLE",
                    "备份副本保全尚不可用",
                )
            expired_target = False
            async with transaction(self._session_factory) as session:
                await self._lock_resource(session, resource_type, resource_id)
                await self._lock_resource_fact(session, resource_type, resource_id)
                locked_holds = await self._holds_for_change(
                    session,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    hold_id=hold_id,
                )
                hold = next((row for row in locked_holds if row.id == hold_id), None)
                if hold is None:
                    raise ApiError(404, "RETENTION_HOLD_NOT_FOUND", "留存保全不存在")
                active = next(
                    (row for row in locked_holds if row.status is RetentionHoldStatus.ACTIVE), None
                )
                if (
                    active is not None
                    and active.expiresAt is not None
                    and as_of >= active.expiresAt
                ):
                    await self._expire(active, as_of=as_of, trace_id=trace_id, session=session)
                if hold.status is RetentionHoldStatus.RELEASED:
                    return hold
                if hold.status is RetentionHoldStatus.EXPIRED or (
                    hold is active and (hold.expiresAt is not None and as_of >= hold.expiresAt)
                ):
                    expired_target = True
                else:
                    hold.status = RetentionHoldStatus.RELEASED
                    hold.releasedAt = as_of
                    hold.releasedById = UUID(identity.user.id)
                    hold.releasedTraceId = str(trace_id)
                    await AuditService(session).record_success(
                        actor_id=UUID(identity.user.id),
                        actor_type=AuditActorType.USER,
                        module="RETENTION",
                        action="RETENTION_HOLD_RELEASED",
                        resource_type="RETENTION_HOLD",
                        resource_id=str(hold.id),
                        trace_id=trace_id,
                    )
                    return hold
            if expired_target:
                raise ApiError(409, "RETENTION_HOLD_EXPIRED", "留存保全已到期")
            raise RuntimeError("retention hold release completed without an outcome")
        except Exception as error:
            await self._record_failure(
                identity,
                trace_id,
                "RETENTION_HOLD",
                str(hold_id),
                self._failure_code(error),
            )
            raise

    async def list_holds(
        self,
        *,
        resource_type: RetentionResourceType | None,
        resource_id: str | None,
        status: RetentionHoldStatus | None,
        page: int,
        page_size: int,
    ) -> tuple[list[RetentionHold], int]:
        filters = []
        if resource_type is not None:
            filters.append(RetentionHold.resourceType == resource_type)
        if resource_id is not None:
            filters.append(RetentionHold.resourceId == resource_id)
        if status is not None:
            filters.append(RetentionHold.status == status)
        async with self._session_factory() as session:
            statement = (
                select(RetentionHold)
                .where(*filters)
                .order_by(RetentionHold.createdAt.desc(), RetentionHold.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            rows = list((await session.scalars(statement)).all())
            total = await session.scalar(
                select(func.count()).select_from(RetentionHold).where(*filters)
            )
        return rows, int(total or 0)

    async def get(self, hold_id: UUID) -> RetentionHold:
        async with self._session_factory() as session:
            hold = await session.get(RetentionHold, hold_id)
        if hold is None:
            raise ApiError(404, "RETENTION_HOLD_NOT_FOUND", "留存保全不存在")
        return hold

    async def expire_due_locked(
        self,
        session: AsyncSession,
        *,
        resource_type: RetentionResourceType,
        resource_id: str,
        as_of: datetime,
        trace_id: UUID,
    ) -> bool:
        """Expire a due active hold while the caller owns its resource lock."""

        hold = await self._active_hold(session, resource_type, resource_id)
        if hold is None or hold.expiresAt is None or as_of < hold.expiresAt:
            return False
        await self._expire(hold, as_of=as_of, trace_id=trace_id, session=session)
        return True

    async def _active_hold(
        self, session: AsyncSession, resource_type: RetentionResourceType, resource_id: str
    ) -> RetentionHold | None:
        return cast(
            RetentionHold | None,
            await session.scalar(
                select(RetentionHold)
                .where(
                    RetentionHold.resourceType == resource_type,
                    RetentionHold.resourceId == resource_id,
                    RetentionHold.status == RetentionHoldStatus.ACTIVE,
                )
                .order_by(RetentionHold.createdAt, RetentionHold.id)
                .with_for_update()
            ),
        )

    async def _holds_for_change(
        self,
        session: AsyncSession,
        *,
        resource_type: RetentionResourceType,
        resource_id: str,
        hold_id: UUID,
    ) -> list[RetentionHold]:
        return list(
            (
                await session.scalars(
                    select(RetentionHold)
                    .where(
                        or_(
                            (RetentionHold.resourceType == resource_type)
                            & (RetentionHold.resourceId == resource_id)
                            & (RetentionHold.status == RetentionHoldStatus.ACTIVE),
                            RetentionHold.id == hold_id,
                        )
                    )
                    .order_by(RetentionHold.createdAt, RetentionHold.id)
                    .with_for_update()
                )
            ).all()
        )

    async def _expire(
        self, hold: RetentionHold, *, as_of: datetime, trace_id: UUID, session: AsyncSession
    ) -> None:
        hold.status = RetentionHoldStatus.EXPIRED
        hold.expiredAt = as_of
        hold.expiredTraceId = str(trace_id)
        await AuditService(session).record_success(
            actor_id=None,
            actor_type=AuditActorType.SYSTEM,
            module="RETENTION",
            action="RETENTION_HOLD_EXPIRED",
            resource_type="RETENTION_HOLD",
            resource_id=str(hold.id),
            trace_id=trace_id,
        )

    @staticmethod
    async def _lock_resource(
        session: AsyncSession, resource_type: RetentionResourceType, resource_id: str
    ) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:resource_key, 0))"),
            {"resource_key": f"retention:{resource_type.value}:{resource_id}"},
        )

    @staticmethod
    async def _lock_resource_fact(
        session: AsyncSession, resource_type: RetentionResourceType, resource_id: str
    ) -> None:
        if resource_type is RetentionResourceType.IMPORT_BATCH:
            row = await session.scalar(
                select(ImportBatch.id).where(ImportBatch.id == UUID(resource_id)).with_for_update()
            )
            if row is None:
                raise ApiError(404, "IMPORT_BATCH_NOT_FOUND", "导入批次不存在")
            return
        if resource_type is RetentionResourceType.AGENT_CONVERSATION:
            row = await session.scalar(
                select(AgentConversation.id)
                .where(AgentConversation.id == UUID(resource_id))
                .with_for_update()
            )
            if row is None:
                raise ApiError(404, "AGENT_CONVERSATION_NOT_FOUND", "Agent 对话不存在")
            return
        raise ApiError(
            409,
            "RETENTION_BACKUP_COPY_UNAVAILABLE",
            "备份副本保全尚不可用",
        )

    @staticmethod
    def _require_human_admin(identity: SessionIdentity) -> None:
        if "admin.config.manage" not in identity.user.permissions:
            raise ApiError(403, "FORBIDDEN", "当前账号无权执行此操作")

    async def _record_failure(
        self,
        identity: SessionIdentity,
        trace_id: UUID,
        resource_type: str,
        resource_id: str,
        code: str,
    ) -> None:
        async with transaction(self._session_factory) as session:
            await AuditService(session).record_failure(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="RETENTION",
                action="RETENTION_HOLD_CHANGE_FAILED",
                resource_type=resource_type,
                resource_id=resource_id,
                trace_id=trace_id,
                failure_code=code,
            )

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, ApiError):
            return str(error.code)
        return "RETENTION_HOLD_CHANGE_FAILED"

    @staticmethod
    def _validate_resource(resource_type: RetentionResourceType, resource_id: str) -> str:
        if resource_type in {
            RetentionResourceType.IMPORT_BATCH,
            RetentionResourceType.AGENT_CONVERSATION,
        }:
            try:
                return str(UUID(resource_id))
            except ValueError:
                raise ApiError(422, "VALIDATION_ERROR", "留存资源标识无效") from None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", resource_id):
            raise ApiError(422, "VALIDATION_ERROR", "留存资源标识无效")
        return resource_id


__all__ = [
    "CreatedHold",
    "LockedRetentionProtectionService",
    "ProtectionResult",
    "RetentionConfigurationRepository",
    "RetentionDecision",
    "RetentionHoldService",
    "RetentionProtectionService",
]
