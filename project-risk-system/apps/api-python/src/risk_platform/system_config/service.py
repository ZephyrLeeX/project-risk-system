"""Transactional application service for system configuration releases."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from unicodedata import normalize
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project, ProjectAlias, ProjectStatus
from risk_platform.retention.configuration import RetentionSettings
from risk_platform.risks.models import Risk, RiskCategory
from risk_platform.shared.errors import ApiError
from risk_platform.system_config.models import ProjectRiskLevel, RiskLevelRule, SystemConfigRelease
from risk_platform.system_config.schemas import (
    ConfigModule,
    ConfigOverview,
    ConfigSnapshot,
    ProjectOptionResponse,
    PublishRequest,
    ReleaseDetail,
    ReleaseItem,
    ReleaseQuery,
    clean_keywords,
)

DEFAULT_MAIL = {
    "syncIntervalMinutes": 30,
    "initialSyncDays": 90,
    "subjectKeywords": ["项目周报", "工作周报", "本周进展", "项目进展", "周工作总结"],
    "riskKeywords": ["风险", "延期", "回款", "逾期", "投诉", "诉讼", "验收", "审计"],
}
DEFAULT_SECURITY = {
    "sessionHours": 8,
    "idleTimeoutMinutes": 30,
    "loginMaxAttempts": 5,
    "loginLockMinutes": 30,
    "passwordMinLength": 12,
}
DEFAULT_NOTIFICATIONS = {
    "mailboxSyncFailure": True,
    "apiKeyExpiry": True,
    "apiKeyExpiryDays": 30,
    "importFailure": True,
    "abnormalLogin": True,
}
DEFAULT_RETENTION = RetentionSettings().model_dump()
IMPACT_SCOPE = ["邮箱同步", "AI风险提取", "Web风险看板", "Agent智能对话", "新建登录会话"]


class SystemConfigService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def overview(self) -> ConfigOverview:
        async with self._session_factory() as session:
            release = await self._latest_or_baseline(session)
            snapshot = await self._snapshot(session, self._settings(release.snapshot))
            month_start = datetime.now(UTC).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            monthly = await session.scalar(
                select(func.count())
                .select_from(SystemConfigRelease)
                .where(
                    SystemConfigRelease.publishedAt >= month_start,
                    SystemConfigRelease.changeCount > 0,
                )
            )
            published_by = await session.scalar(
                select(User.displayName).where(User.id == release.publishedById)
            )
            return self._overview(release, snapshot, int(monthly or 0), published_by)

    async def project_options(self) -> list[ProjectOptionResponse]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Project, Department.name)
                    .outerjoin(Department, Department.id == Project.departmentId)
                    .where(Project.status != ProjectStatus.ARCHIVED)
                    .order_by(Project.name.asc())
                    .limit(1_000)
                )
            ).all()
            return [
                ProjectOptionResponse(
                    id=str(p.id),
                    externalCode=p.externalCode,
                    name=p.name,
                    departmentName=department,
                )
                for p, department in rows
            ]

    async def releases(self, query: ReleaseQuery) -> list[ReleaseItem]:
        async with self._session_factory() as session:
            statement = (
                select(SystemConfigRelease, User.displayName)
                .outerjoin(User, User.id == SystemConfigRelease.publishedById)
                .order_by(SystemConfigRelease.publishedAt.desc())
                .limit(query.limit)
            )
            if query.module and query.module != "ALL":
                statement = statement.where(SystemConfigRelease.module == query.module)
            return [
                self._release_item(row, name)
                for row, name in (await session.execute(statement)).all()
            ]

    async def release_detail(self, release_id: UUID) -> ReleaseDetail:
        async with self._session_factory() as session:
            row, name = (
                await session.execute(
                    select(SystemConfigRelease, User.displayName)
                    .outerjoin(User, User.id == SystemConfigRelease.publishedById)
                    .where(SystemConfigRelease.id == release_id)
                )
            ).one_or_none() or (None, None)
            if row is None:
                raise ApiError(404, "NOT_FOUND", "配置版本不存在")
            item = self._release_item(row, name)
            snapshot = ConfigSnapshot.model_validate(row.snapshot)
            return ReleaseDetail(
                **item.model_dump(),
                beforeSnapshot=self._snapshot_model(row.beforeSnapshot),
                snapshot=snapshot,
            )

    async def publish(
        self, payload: PublishRequest, identity: SessionIdentity, trace_id: UUID
    ) -> ConfigOverview:
        self._validate(payload)
        try:
            async with self._mutation(identity, trace_id) as session:
                latest = await self._latest(session)
                before = await self._snapshot(
                    session, self._settings(latest.snapshot) if latest else None
                )
                await self._write_current(session, payload)
                after = await self._snapshot(
                    session,
                    {
                        "mail": payload.mail.model_dump(),
                        "security": payload.security.model_dump(),
                        "notifications": payload.notifications.model_dump(),
                        "retention": payload.retention.model_dump(),
                    },
                )
                version = self._next_version(latest.version if latest else "V12.3")
                release = SystemConfigRelease(
                    version=version,
                    module=payload.module,
                    changeCount=payload.changeCount,
                    changeSummary=payload.changeSummary.strip(),
                    impactScope=IMPACT_SCOPE,
                    beforeSnapshot=before.model_dump(mode="json"),
                    snapshot=after.model_dump(mode="json"),
                    publishedById=UUID(identity.user.id),
                    traceId=str(trace_id),
                )
                session.add(release)
                await session.flush()
                await AuditService(session).record_success(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="SYSTEM_CONFIG",
                    action="SYSTEM_CONFIG_PUBLISHED",
                    resource_type="SYSTEM_CONFIG_RELEASE",
                    resource_id=str(release.id),
                    trace_id=trace_id,
                )
            return await self.overview()
        except IntegrityError as error:
            await self._failure_audit(identity, trace_id, "CONFLICT")
            raise ApiError(409, "CONFLICT", "风险类别编码或项目别名重复，请检查后重试") from error  # noqa: RUF001
        except ApiError as error:
            await self._failure_audit(identity, trace_id, error.code)
            raise
        except Exception:
            await self._failure_audit(identity, trace_id, "SYSTEM_CONFIG_PUBLISH_FAILED")
            raise

    async def runtime_mail_settings(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            release = await self._latest_or_baseline(session)
            return cast(dict[str, Any], self._settings(release.snapshot)["mail"])

    @asynccontextmanager
    async def _mutation(
        self, identity: SessionIdentity, trace_id: UUID
    ) -> AsyncIterator[AsyncSession]:
        async with transaction(self._session_factory) as session:
            yield session

    async def _failure_audit(self, identity: SessionIdentity, trace_id: UUID, code: str) -> None:
        async with transaction(self._session_factory) as session:
            await AuditService(session).record_failure(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="SYSTEM_CONFIG",
                action="SYSTEM_CONFIG_PUBLISH_FAILED",
                resource_type="SYSTEM_CONFIG_RELEASE",
                resource_id=None,
                trace_id=trace_id,
                failure_code=code,
            )

    async def _latest(self, session: AsyncSession) -> SystemConfigRelease | None:
        return cast(
            SystemConfigRelease | None,
            await session.scalar(
                select(SystemConfigRelease)
                .order_by(SystemConfigRelease.publishedAt.desc())
                .limit(1)
            ),
        )

    async def _latest_or_baseline(self, session: AsyncSession) -> SystemConfigRelease:
        release = await self._latest(session)
        if release:
            return release
        snapshot = await self._snapshot(session)
        release = SystemConfigRelease(
            version="V12.3",
            module="ALL",
            changeCount=0,
            changeSummary="初始化现有风险规则、邮箱识别、项目别名与安全参数",
            impactScope=["现有配置基线"],
            snapshot=snapshot.model_dump(mode="json"),
            traceId=str(uuid4()),
        )
        session.add(release)
        await session.commit()
        return release

    async def _write_current(self, session: AsyncSession, payload: PublishRequest) -> None:
        retained: list[UUID] = []
        for item in payload.categories:
            code = self._code(item.code)
            row = (
                await session.scalar(select(RiskCategory).where(RiskCategory.id == item.id))
                if item.id
                else await session.scalar(select(RiskCategory).where(RiskCategory.code == code))
            )
            if row is None:
                row = RiskCategory(
                    code=code,
                    name=item.name.strip(),
                    keywords=clean_keywords(item.keywords),
                    colorToken=item.colorToken.upper(),
                    description=item.description.strip() if item.description else None,
                    defaultLevel=ProjectRiskLevel(item.defaultLevel) if item.defaultLevel else None,
                    sortOrder=item.sortOrder,
                    isActive=item.isActive,
                )
                session.add(row)
            else:
                (
                    row.code,
                    row.name,
                    row.keywords,
                    row.colorToken,
                    row.description,
                    row.defaultLevel,
                    row.sortOrder,
                    row.isActive,
                ) = (
                    code,
                    item.name.strip(),
                    clean_keywords(item.keywords),
                    item.colorToken.upper(),
                    item.description.strip() if item.description else None,
                    ProjectRiskLevel(item.defaultLevel) if item.defaultLevel else None,
                    item.sortOrder,
                    item.isActive,
                )
            await session.flush()
            retained.append(row.id)
        if retained:
            for row in (
                await session.scalars(select(RiskCategory).where(RiskCategory.id.not_in(retained)))
            ).all():
                row.isActive = False
        for level_item in payload.levels:
            level_row = await session.scalar(
                select(RiskLevelRule).where(RiskLevelRule.level == level_item.level)
            )
            if level_row is None:
                level_row = RiskLevelRule(
                    level=ProjectRiskLevel(level_item.level),
                    displayName=level_item.displayName.strip(),
                    colorToken=level_item.colorToken.upper(),
                    criteria=level_item.criteria.strip(),
                    keywords=clean_keywords(level_item.keywords),
                    sortOrder=level_item.sortOrder,
                    isActive=level_item.isActive,
                )
                session.add(level_row)
            else:
                (
                    level_row.displayName,
                    level_row.colorToken,
                    level_row.criteria,
                    level_row.keywords,
                    level_row.sortOrder,
                    level_row.isActive,
                ) = (
                    level_item.displayName.strip(),
                    level_item.colorToken.upper(),
                    level_item.criteria.strip(),
                    cast(JSONValue, clean_keywords(level_item.keywords)),
                    level_item.sortOrder,
                    level_item.isActive,
                )
        retained_aliases: list[UUID] = []
        for alias_item in payload.aliases:
            normalized = self._alias(alias_item.alias)
            alias_row = (
                await session.scalar(select(ProjectAlias).where(ProjectAlias.id == alias_item.id))
                if alias_item.id
                else await session.scalar(
                    select(ProjectAlias).where(ProjectAlias.normalizedAlias == normalized)
                )
            )
            if alias_row is None:
                alias_row = ProjectAlias(
                    projectId=alias_item.projectId,
                    alias=alias_item.alias.strip(),
                    normalizedAlias=normalized,
                    source=alias_item.source.strip() or "系统管理员",
                    note=alias_item.note.strip() if alias_item.note else None,
                    isActive=alias_item.isActive,
                )
                session.add(alias_row)
            else:
                (
                    alias_row.projectId,
                    alias_row.alias,
                    alias_row.normalizedAlias,
                    alias_row.source,
                    alias_row.note,
                    alias_row.isActive,
                ) = (
                    alias_item.projectId,
                    alias_item.alias.strip(),
                    normalized,
                    alias_item.source.strip() or "系统管理员",
                    alias_item.note.strip() if alias_item.note else None,
                    alias_item.isActive,
                )
            await session.flush()
            retained_aliases.append(alias_row.id)
        for row in (
            await session.scalars(
                select(ProjectAlias).where(ProjectAlias.id.not_in(retained_aliases))
            )
        ).all():
            row.isActive = False

    async def _snapshot(
        self, session: AsyncSession, settings: dict[str, Any] | None = None
    ) -> ConfigSnapshot:
        categories = (
            await session.scalars(
                select(RiskCategory).order_by(RiskCategory.sortOrder.asc(), RiskCategory.name.asc())
            )
        ).all()
        levels = (
            await session.scalars(
                select(RiskLevelRule)
                .where(RiskLevelRule.level != ProjectRiskLevel.UNKNOWN)
                .order_by(RiskLevelRule.sortOrder.asc())
            )
        ).all()
        alias_rows = (
            await session.execute(
                select(ProjectAlias, Project, User.displayName)
                .join(Project, Project.id == ProjectAlias.projectId)
                .outerjoin(User, User.id == Project.managerId)
                .order_by(Project.name.asc(), ProjectAlias.alias.asc())
            )
        ).all()
        result: dict[str, Any] = {
            "categories": [],
            "levels": [],
            "aliases": [],
            "mail": (settings or {}).get("mail", DEFAULT_MAIL),
            "security": (settings or {}).get("security", DEFAULT_SECURITY),
            "notifications": (settings or {}).get("notifications", DEFAULT_NOTIFICATIONS),
            "retention": (settings or {}).get("retention", DEFAULT_RETENTION),
        }
        for row in categories:
            risk_count = await session.scalar(
                select(func.count()).select_from(Risk).where(Risk.categoryId == row.id)
            )
            result["categories"].append(
                {
                    "id": str(row.id),
                    "code": row.code,
                    "name": row.name,
                    "keywords": row.keywords if isinstance(row.keywords, list) else [],
                    "colorToken": row.colorToken,
                    "description": row.description,
                    "defaultLevel": row.defaultLevel,
                    "sortOrder": row.sortOrder,
                    "isActive": row.isActive,
                    "riskCount": int(risk_count or 0),
                }
            )
        result["levels"] = [
            {
                "level": row.level,
                "displayName": row.displayName,
                "colorToken": row.colorToken,
                "criteria": row.criteria,
                "keywords": row.keywords if isinstance(row.keywords, list) else [],
                "sortOrder": row.sortOrder,
                "isActive": row.isActive,
            }
            for row in levels
        ]
        result["aliases"] = [
            {
                "id": str(alias.id),
                "projectId": str(alias.projectId),
                "projectName": project.name,
                "projectCode": project.externalCode,
                "projectOwnerName": manager_name or project.deliveryOwnerName,
                "alias": alias.alias,
                "source": alias.source,
                "note": alias.note,
                "isActive": alias.isActive,
                "hitCount": alias.hitCount,
                "lastHitAt": alias.lastHitAt.isoformat().replace("+00:00", "Z")
                if alias.lastHitAt
                else None,
            }
            for alias, project, manager_name in alias_rows
        ]
        return ConfigSnapshot.model_validate(result)

    @staticmethod
    def _settings(snapshot: Any) -> dict[str, Any]:
        value = snapshot if isinstance(snapshot, dict) else {}
        return {
            "mail": {**DEFAULT_MAIL, **value.get("mail", {})},
            "security": {**DEFAULT_SECURITY, **value.get("security", {})},
            "notifications": {**DEFAULT_NOTIFICATIONS, **value.get("notifications", {})},
            "retention": {**DEFAULT_RETENTION, **value.get("retention", {})},
        }

    @staticmethod
    def _snapshot_model(value: Any) -> ConfigSnapshot | None:
        return ConfigSnapshot.model_validate(value) if value is not None else None

    def _overview(
        self,
        release: SystemConfigRelease,
        snapshot: ConfigSnapshot,
        monthly: int,
        published_by: str | None,
    ) -> ConfigOverview:
        return ConfigOverview(
            version=release.version,
            publishedAt=release.publishedAt.astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            publishedBy=published_by or "系统初始化",
            changeSummary=release.changeSummary,
            activeConfigCount=sum(item.isActive for item in snapshot.categories)
            + sum(item.isActive for item in snapshot.levels)
            + 8,
            activeCategoryCount=sum(item.isActive for item in snapshot.categories),
            activeLevelCount=sum(item.isActive for item in snapshot.levels),
            monthlyChangeCount=monthly,
            lastMailboxSyncAt=None,
            nextMailboxSyncAt=None,
            authorizedMailboxCount=0,
            snapshot=snapshot,
        )

    def _release_item(self, row: SystemConfigRelease, name: str | None) -> ReleaseItem:
        return ReleaseItem(
            id=str(row.id),
            version=row.version,
            module=cast(ConfigModule, row.module),
            changeCount=row.changeCount,
            changeSummary=row.changeSummary,
            impactScope=cast(list[str], row.impactScope)
            if isinstance(row.impactScope, list)
            else [],
            publishedAt=row.publishedAt.astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            publishedBy=name or "系统初始化",
            traceId=row.traceId,
        )

    @staticmethod
    def _code(value: str) -> str:
        import re

        code = re.sub(r"[\s-]+", "_", value.strip().upper())
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code):
            raise ApiError(400, "BAD_REQUEST", f"风险类别编码“{value}”格式不正确")
        return code

    @staticmethod
    def _alias(value: str) -> str:
        import re

        return re.sub(r"\s+", "", normalize("NFKC", value).strip().casefold())

    def _validate(self, payload: PublishRequest) -> None:
        codes = [self._code(item.code) for item in payload.categories]
        aliases = [self._alias(item.alias) for item in payload.aliases]
        if len(codes) != len(set(codes)):
            raise ApiError(409, "CONFLICT", "风险类别编码不能重复")
        if not any(item.isActive for item in payload.categories):
            raise ApiError(400, "BAD_REQUEST", "至少需要启用一个风险类别")
        if any(not item for item in aliases) or len(aliases) != len(set(aliases)):
            raise ApiError(409, "CONFLICT", "项目别名不能为空且不能重复")

    @staticmethod
    def _next_version(version: str) -> str:
        import re

        match = re.fullmatch(r"V(\d+)\.(\d+)", version)
        return f"V{match.group(1)}.{int(match.group(2)) + 1}" if match else "V12.4"


__all__ = ["SystemConfigService"]
