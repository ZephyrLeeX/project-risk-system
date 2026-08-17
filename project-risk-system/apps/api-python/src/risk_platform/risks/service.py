"""Scoped risk lifecycle, mutation and timeline query services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from risk_platform.admin.models import Department, User
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.risks.models import (
    ProjectRiskLevel,
    Risk,
    RiskCategory,
    RiskSourceType,
    RiskStatus,
)
from risk_platform.risks.schemas import (
    LifecycleRequest,
    ResolvedRiskPage,
    RiskCategoryOption,
    RiskDetail,
    RiskFilterOptions,
    RiskItem,
    RiskPage,
    RiskQuery,
    SameProjectRisk,
    TimelineDetail,
    TimelineItem,
    TimelinePage,
    TimelineQuery,
)
from risk_platform.shared.errors import ApiError
from risk_platform.timeline.models import RiskTimelineEvent, RiskTimelineEventType
from risk_platform.timeline.policy import event_presentation
from risk_platform.todos.models import ActionItem, ActionItemStatus
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import invalidate_risk


@dataclass(frozen=True, slots=True)
class RiskCreate:
    project_id: UUID
    category_id: UUID
    title: str
    description: str
    level: ProjectRiskLevel
    source_type: RiskSourceType
    dedupe_fingerprint: str
    evidence: str | None = None
    suggestion: str | None = None
    source_batch_id: UUID | None = None
    source_ref_id: UUID | None = None
    reporter_user_id: UUID | None = None
    reporter_name: str | None = None
    week_code: str | None = None
    actor_name: str | None = None


class RisksService:
    """Own risk writes and scoped risk/timeline reads; callers get one transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self, command: RiskCreate, *, actor_id: UUID | None = None, trace_id: UUID | None = None
    ) -> Risk:
        async with transaction(self._session_factory) as session:
            return await self.create_in_session(
                session, command, actor_id=actor_id, trace_id=trace_id
            )

    async def create_in_session(
        self,
        session: AsyncSession,
        command: RiskCreate,
        *,
        actor_id: UUID | None = None,
        trace_id: UUID | None = None,
    ) -> Risk:
        if not command.title.strip() or not command.description.strip():
            raise ApiError(400, "BAD_REQUEST", "风险标题和描述不能为空")
        existing = await session.scalar(
            select(Risk).where(Risk.dedupeFingerprint == command.dedupe_fingerprint)
        )
        if existing is not None:
            return existing
        risk = Risk(
            projectId=command.project_id,
            categoryId=command.category_id,
            title=command.title.strip(),
            description=command.description.strip(),
            evidence=command.evidence,
            suggestion=command.suggestion,
            level=command.level,
            sourceType=command.source_type,
            dedupeFingerprint=command.dedupe_fingerprint,
            sourceBatchId=command.source_batch_id,
            sourceRefId=command.source_ref_id,
            reporterUserId=command.reporter_user_id or actor_id,
            reporterNameSource=command.reporter_name,
            weekCode=command.week_code,
        )
        try:
            async with session.begin_nested():
                session.add(risk)
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(Risk).where(Risk.dedupeFingerprint == command.dedupe_fingerprint)
            )
            if existing is None:
                raise
            return cast(Risk, existing)
        project = await session.get(Project, command.project_id)
        if project is None:
            raise ApiError(404, "NOT_FOUND", "项目不存在")
        todo = await TodosService(self._session_factory).ensure_for_risk(
            session, risk, owner_name=project.deliveryOwnerName, actor_id=actor_id
        )
        occurred_at = datetime.now(UTC)
        actor_type = AuditActorType.USER if actor_id is not None else AuditActorType.SYSTEM
        actor_uuid = actor_id
        session.add(
            RiskTimelineEvent(
                projectId=risk.projectId,
                riskId=risk.id,
                eventType=RiskTimelineEventType.RISK_CREATED,
                title="新增风险",
                description=risk.description,
                actorUserId=actor_uuid,
                actorNameSource=command.actor_name or command.reporter_name,
                occurredAt=occurred_at,
            )
        )
        session.add(
            RiskTimelineEvent(
                projectId=risk.projectId,
                riskId=risk.id,
                actionItemId=todo.id,
                eventType=RiskTimelineEventType.ACTION_CREATED,
                title="生成待办",
                description=todo.description,
                actorUserId=actor_uuid,
                actorNameSource=command.actor_name or command.reporter_name,
                occurredAt=occurred_at,
            )
        )
        await AuditService(session).record_success(
            actor_id=actor_uuid,
            actor_type=actor_type,
            module="RISK",
            action="RISK_CREATED",
            resource_type="RISK",
            resource_id=str(risk.id),
            trace_id=trace_id or uuid4(),
            project_id=risk.projectId,
        )
        return risk

    async def resolve(
        self, identity: SessionIdentity, risk_id: UUID, payload: LifecycleRequest, trace_id: UUID
    ) -> RiskDetail:
        async with transaction(self._session_factory) as session:
            row = await self.resolve_in_session(session, identity, risk_id, payload, trace_id)
            risk, project, department, category, reporter, resolved_by = row
        return _detail((risk, project, department, category, reporter, resolved_by))

    async def update_agent_in_session(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        risk_id: UUID,
        fields: Mapping[str, object],
        *,
        trace_id: UUID,
    ) -> Risk:
        """Apply the T051 allowlist through the risk domain boundary."""
        row = await self._risk_row(session, identity, risk_id, for_update=True)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "风险不存在或不在当前数据范围内")
        risk, project, _department, _category, _reporter, _resolved_by = row
        allowed = {"title", "description", "level", "category", "evidence", "suggestion"}
        if not fields or set(fields) - allowed:
            raise ApiError(422, "VALIDATION_ERROR", "风险字段不在允许修改范围内")
        for key, value in fields.items():
            if key in {"title", "description"}:
                if not isinstance(value, str) or not value.strip():
                    raise ApiError(422, "VALIDATION_ERROR", "风险标题和描述不能为空")
                setattr(risk, key, value.strip())
            elif key == "level":
                try:
                    risk.level = ProjectRiskLevel(str(value))
                except ValueError:
                    raise ApiError(422, "VALIDATION_ERROR", "风险等级无效") from None
            elif key == "category":
                try:
                    category_id = UUID(str(value))
                except ValueError:
                    raise ApiError(422, "VALIDATION_ERROR", "风险分类无效") from None
                category = await session.scalar(
                    select(RiskCategory).where(
                        RiskCategory.id == category_id, RiskCategory.isActive.is_(True)
                    )
                )
                if category is None:
                    raise ApiError(409, "RISK_CATEGORY_STALE", "风险分类已失效")
                risk.categoryId = category.id
            elif key in {"evidence", "suggestion"}:
                if value is not None and not isinstance(value, str):
                    raise ApiError(422, "VALIDATION_ERROR", "风险字段格式无效")
                setattr(risk, key, value.strip() if isinstance(value, str) else None)
        await session.flush()
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="RISK",
            action="RISK_UPDATED",
            resource_type="RISK",
            resource_id=str(risk.id),
            trace_id=trace_id,
            project_id=project.id,
        )
        return cast(Risk, risk)

    async def resolve_in_session(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        risk_id: UUID,
        payload: LifecycleRequest,
        trace_id: UUID,
    ) -> tuple[Any, ...]:
        """Resolve through the domain service inside a caller-owned transaction."""
        row = await self._risk_row(session, identity, risk_id, for_update=True)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "风险不存在或不在当前数据范围内")
        risk, project, _department, _category, _reporter, _resolved_by = row
        if risk.status is RiskStatus.RESOLVED:
            raise ApiError(400, "RISK_ALREADY_RESOLVED", "该风险已经解除，无需重复操作")  # noqa: RUF001
        now = datetime.now(UTC)
        risk.status, risk.resolvedAt = RiskStatus.RESOLVED, now
        risk.resolvedById, risk.resolutionReason = UUID(identity.user.id), payload.reason
        todos = list(
            (
                await session.scalars(
                    select(ActionItem).where(ActionItem.riskId == risk.id).with_for_update()
                )
            ).all()
        )
        for todo in todos:
            if todo.status is not ActionItemStatus.COMPLETED:
                old = todo.status
                todo.status, todo.completedAt, todo.completedById = (
                    ActionItemStatus.COMPLETED,
                    now,
                    UUID(identity.user.id),
                )
                todo.completionNote = _resolution_note(todo.completionNote, payload.reason)
                session.add(
                    _event(
                        risk,
                        project.id,
                        todo.id,
                        RiskTimelineEventType.ACTION_COMPLETED,
                        "待办事项随风险解除完成",
                        f"风险已解除，关联待办同步完成：{payload.reason}",  # noqa: RUF001
                        old.value,
                        todo.status.value,
                        identity,
                        now,
                    )
                )
        session.add(
            _event(
                risk,
                project.id,
                todos[0].id if todos else None,
                RiskTimelineEventType.RISK_RESOLVED,
                "风险已解除",
                payload.reason,
                RiskStatus.ACTIVE.value,
                RiskStatus.RESOLVED.value,
                identity,
                now,
            )
        )
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="RISK",
            action="RISK_RESOLVED",
            resource_type="RISK",
            resource_id=str(risk.id),
            trace_id=trace_id,
            project_id=project.id,
        )
        await invalidate_risk(session, risk.id)
        return row

    async def reopen(
        self, identity: SessionIdentity, risk_id: UUID, payload: LifecycleRequest, trace_id: UUID
    ) -> RiskDetail:
        async with transaction(self._session_factory) as session:
            row = await self._risk_row(session, identity, risk_id, for_update=True)
            if row is None:
                raise ApiError(404, "NOT_FOUND", "风险不存在或不在当前数据范围内")
            risk, project, department, category, reporter, resolved_by = row
            if risk.status is RiskStatus.ACTIVE:
                raise ApiError(400, "RISK_ALREADY_ACTIVE", "该风险当前处于有效状态，无需重新打开")  # noqa: RUF001
            now = datetime.now(UTC)
            risk.status, risk.resolvedAt, risk.resolvedById, risk.resolutionReason = (
                RiskStatus.ACTIVE,
                None,
                None,
                None,
            )
            todos = list(
                (
                    await session.scalars(
                        select(ActionItem).where(ActionItem.riskId == risk.id).with_for_update()
                    )
                ).all()
            )
            if not todos:
                todo = None
                todo = await TodosService(self._session_factory).ensure_for_risk(
                    session,
                    risk,
                    owner_name=project.deliveryOwnerName,
                    actor_id=UUID(identity.user.id),
                )
                session.add(
                    _event(
                        risk,
                        project.id,
                        todo.id,
                        RiskTimelineEventType.ACTION_CREATED,
                        "风险重启后生成处理待办",
                        todo.description,
                        None,
                        todo.status.value,
                        identity,
                        now,
                    )
                )
            else:
                todo = todos[0]
                for todo_item in todos:
                    if todo_item.status is not ActionItemStatus.PENDING:
                        old = todo_item.status
                        todo_item.status, todo_item.completionNote = ActionItemStatus.PENDING, None
                        todo_item.completedAt, todo_item.completedById = None, None
                        session.add(
                            _event(
                                risk,
                                project.id,
                                todo_item.id,
                                RiskTimelineEventType.ACTION_STATUS_CHANGED,
                                "风险重启后待办恢复处理",
                                f"风险重新打开：{payload.reason}",  # noqa: RUF001
                                old.value,
                                todo_item.status.value,
                                identity,
                                now,
                            )
                        )
            session.add(
                _event(
                    risk,
                    project.id,
                    todo.id,
                    RiskTimelineEventType.RISK_REOPENED,
                    "风险重新进入跟踪",
                    payload.reason,
                    RiskStatus.RESOLVED.value,
                    RiskStatus.ACTIVE.value,
                    identity,
                    now,
                )
            )
            await AuditService(session).record_success(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="RISK",
                action="RISK_REOPENED",
                resource_type="RISK",
                resource_id=str(risk.id),
                trace_id=trace_id,
                project_id=project.id,
            )
            await invalidate_risk(session, risk.id)
        return _detail((risk, project, department, category, reporter, resolved_by))

    async def detail(self, identity: SessionIdentity, risk_id: UUID) -> RiskDetail:
        async with self._session_factory() as session:
            row = await self._risk_row(session, identity, risk_id)
            same_project: Sequence[Any] = ()
            if row is not None:
                risk = cast(Risk, row[0])
                same_project = (
                    await session.execute(
                        select(Risk.id, Risk.title, Risk.level, Risk.status, RiskCategory.name)
                        .join(RiskCategory, RiskCategory.id == Risk.categoryId)
                        .where(Risk.projectId == risk.projectId, Risk.id != risk.id)
                        .order_by(Risk.status, Risk.level, Risk.updatedAt.desc())
                        .limit(10)
                    )
                ).all()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "风险不存在或不在当前数据范围内")
        return _detail(row, same_project)

    async def filter_options(self, identity: SessionIdentity) -> RiskFilterOptions:
        categories = await self.list_categories(identity)
        async with self._session_factory() as session:
            owners = (
                await session.scalars(
                    select(Project.deliveryOwnerName)
                    .where(
                        project_scope_predicate(
                            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                        ),
                        Project.deliveryOwnerName.is_not(None),
                    )
                    .distinct()
                    .order_by(Project.deliveryOwnerName)
                )
            ).all()
        return RiskFilterOptions(
            categories=categories,
            owners=[item for item in owners if item is not None],
        )

    async def list_categories(self, identity: SessionIdentity) -> list[RiskCategoryOption]:
        """Return the approved active risk taxonomy through a typed read boundary."""
        del identity
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(RiskCategory)
                    .where(RiskCategory.isActive.is_(True))
                    .order_by(RiskCategory.sortOrder, RiskCategory.name)
                )
            ).all()
        return [RiskCategoryOption(id=item.id, code=item.code, name=item.name) for item in rows]

    async def list(
        self,
        identity: SessionIdentity,
        query: RiskQuery,
        *,
        resolved: bool = False,
        project_id: UUID | None = None,
    ) -> RiskPage | ResolvedRiskPage:
        async with self._session_factory() as session:
            conditions = self._risk_conditions(
                identity, query, resolved=resolved, project_id=project_id
            )
            rows = (
                await session.execute(
                    self._risk_statement(conditions)
                    .order_by(Risk.updatedAt.desc())
                    .offset((query.page - 1) * query.pageSize)
                    .limit(query.pageSize)
                )
            ).all()
            total = (
                await session.scalar(
                    select(func.count())
                    .select_from(Risk)
                    .join(Project, Project.id == Risk.projectId)
                    .where(*conditions)
                )
                or 0
            )
            items = [_item(cast(tuple[Any, ...], row)) for row in rows]
            if not resolved:
                return RiskPage(items=items, page=query.page, pageSize=query.pageSize, total=total)
            owners = (
                await session.scalars(
                    select(Project.deliveryOwnerName)
                    .where(
                        project_scope_predicate(
                            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                        ),
                        Project.deliveryOwnerName.is_not(None),
                    )
                    .distinct()
                    .order_by(Project.deliveryOwnerName)
                )
            ).all()
            latest = await session.scalar(
                select(func.max(Risk.resolvedAt))
                .select_from(Risk)
                .join(Project, Project.id == Risk.projectId)
                .where(
                    project_scope_predicate(
                        UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                    ),
                    Risk.status == RiskStatus.RESOLVED,
                )
            )
            return ResolvedRiskPage(
                items=items,
                page=query.page,
                pageSize=query.pageSize,
                total=total,
                owners=[owner for owner in owners if owner is not None],
                updatedAt=_iso(latest),
                dataScope=DataScopeType(identity.user.dataScope),
            )

    async def list_for_project(
        self, identity: SessionIdentity, project_id: UUID, query: RiskQuery
    ) -> RiskPage:
        page = await self.list(identity, query, project_id=project_id)
        assert isinstance(page, RiskPage)
        return page

    async def timeline(self, identity: SessionIdentity, query: TimelineQuery) -> TimelinePage:
        async with self._session_factory() as session:
            conditions = self._timeline_conditions(identity, query)
            stmt = self._timeline_statement(conditions).order_by(
                RiskTimelineEvent.occurredAt.desc(), RiskTimelineEvent.createdAt.desc()
            )
            rows = (
                await session.execute(
                    stmt.offset((query.page - 1) * query.pageSize).limit(query.pageSize)
                )
            ).all()
            total = (
                await session.scalar(
                    select(func.count())
                    .select_from(RiskTimelineEvent)
                    .join(Project, Project.id == RiskTimelineEvent.projectId)
                    .where(*conditions)
                )
                or 0
            )
            scoped = [
                project_scope_predicate(
                    UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                )
            ]
            counts = {}
            for name, events in {
                "riskCreated": [RiskTimelineEventType.RISK_CREATED],
                "riskChanged": [
                    RiskTimelineEventType.RISK_UPDATED,
                    RiskTimelineEventType.LEVEL_CHANGED,
                    RiskTimelineEventType.RISK_REOPENED,
                ],
                "actionProgress": [
                    RiskTimelineEventType.ACTION_CREATED,
                    RiskTimelineEventType.ACTION_UPDATED,
                    RiskTimelineEventType.ACTION_STATUS_CHANGED,
                    RiskTimelineEventType.ACTION_COMPLETED,
                ],
                "resolved": [RiskTimelineEventType.RISK_RESOLVED],
            }.items():
                counts[name] = (
                    await session.scalar(
                        select(func.count())
                        .select_from(RiskTimelineEvent)
                        .join(Project, Project.id == RiskTimelineEvent.projectId)
                        .join(Risk, Risk.id == RiskTimelineEvent.riskId)
                        .where(*scoped, RiskTimelineEvent.eventType.in_(events))
                    )
                    or 0
                )
            latest = await session.scalar(
                select(func.max(RiskTimelineEvent.occurredAt))
                .select_from(RiskTimelineEvent)
                .join(Project, Project.id == RiskTimelineEvent.projectId)
                .where(*scoped)
            )
            projects = (
                await session.execute(
                    select(Project.id, Project.name)
                    .where(*scoped)
                    .where(
                        select(RiskTimelineEvent.id)
                        .where(RiskTimelineEvent.projectId == Project.id)
                        .exists()
                    )
                    .order_by(Project.name)
                )
            ).all()
        return TimelinePage(
            items=[_timeline_item(cast(tuple[Any, ...], row)) for row in rows],
            page=query.page,
            pageSize=query.pageSize,
            total=total,
            summary={"total": sum(counts.values()), **counts},
            projects=[{"id": i, "name": n} for i, n in projects],
            updatedAt=_iso(latest),
            dataScope=DataScopeType(identity.user.dataScope),
        )

    async def timeline_detail(self, identity: SessionIdentity, event_id: UUID) -> TimelineDetail:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    self._timeline_statement(
                        [
                            RiskTimelineEvent.id == event_id,
                            project_scope_predicate(
                                UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                            ),
                        ]
                    )
                )
            ).first()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "时间线事件不存在或不在当前数据范围内")
        event, project, department, risk, category, reporter = cast(tuple[Any, ...], row)
        item = _timeline_item((event, project, department, risk, category, reporter))
        metadata = event.metadata_ if isinstance(event.metadata_, dict) else None
        return TimelineDetail(
            **item.model_dump(),
            riskDescription=risk.description,
            riskEvidence=risk.evidence,
            riskSuggestion=risk.suggestion,
            detectedAt=_iso(risk.detectedAt) or "",
            resolvedAt=_iso(risk.resolvedAt),
            resolutionReason=risk.resolutionReason,
            metadata=metadata,
        )

    async def _risk_row(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        risk_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[Any, ...] | None:
        statement = self._risk_statement(
            [
                Risk.id == risk_id,
                project_scope_predicate(
                    UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                ),
            ]
        )
        if for_update:
            statement = statement.with_for_update(of=Risk)
        return cast(tuple[Any, ...] | None, (await session.execute(statement)).first())

    @staticmethod
    def _risk_statement(conditions: Sequence[Any]) -> Any:
        reporter, resolved = aliased(User), aliased(User)
        return (
            select(Risk, Project, Department, RiskCategory, reporter, resolved)
            .join(Project, Project.id == Risk.projectId)
            .outerjoin(Department, Department.id == Project.departmentId)
            .join(RiskCategory, RiskCategory.id == Risk.categoryId)
            .outerjoin(reporter, reporter.id == Risk.reporterUserId)
            .outerjoin(resolved, resolved.id == Risk.resolvedById)
            .where(*conditions)
        )

    @staticmethod
    def _timeline_statement(conditions: Sequence[Any]) -> Any:
        reporter = aliased(User)
        return (
            select(RiskTimelineEvent, Project, Department, Risk, RiskCategory, reporter)
            .join(Project, Project.id == RiskTimelineEvent.projectId)
            .outerjoin(Department, Department.id == Project.departmentId)
            .join(Risk, Risk.id == RiskTimelineEvent.riskId)
            .join(RiskCategory, RiskCategory.id == Risk.categoryId)
            .outerjoin(reporter, reporter.id == Risk.reporterUserId)
            .where(*conditions)
        )

    @staticmethod
    def _risk_conditions(
        identity: SessionIdentity,
        query: RiskQuery,
        *,
        resolved: bool,
        project_id: UUID | None = None,
    ) -> Sequence[Any]:
        conditions: list[Any] = [
            project_scope_predicate(UUID(identity.user.id), DataScopeType(identity.user.dataScope)),
            Risk.status == (RiskStatus.RESOLVED if resolved else RiskStatus.ACTIVE),
        ]
        if query.keyword and (value := query.keyword.strip()):
            pattern = f"%{value}%"
            conditions.append(
                or_(
                    Risk.title.ilike(pattern),
                    Risk.description.ilike(pattern),
                    Project.name.ilike(pattern),
                )
            )
        if project_id is not None:
            conditions.append(Risk.projectId == project_id)
        if query.level:
            conditions.append(Risk.level == query.level)
        if query.categoryId:
            conditions.append(Risk.categoryId == query.categoryId)
        if query.owner:
            conditions.append(Project.deliveryOwnerName == query.owner.strip())
        if query.sourceType:
            conditions.append(Risk.sourceType == query.sourceType)
        return conditions

    @staticmethod
    def _timeline_conditions(identity: SessionIdentity, query: TimelineQuery) -> Sequence[Any]:
        conditions: list[Any] = [
            project_scope_predicate(UUID(identity.user.id), DataScopeType(identity.user.dataScope))
        ]
        if query.keyword and (value := query.keyword.strip()):
            pattern = f"%{value}%"
            conditions.append(
                or_(
                    RiskTimelineEvent.title.ilike(pattern),
                    RiskTimelineEvent.description.ilike(pattern),
                    Project.name.ilike(pattern),
                    Risk.title.ilike(pattern),
                )
            )
        if query.level:
            conditions.append(Risk.level == query.level)
        if query.eventType:
            conditions.append(RiskTimelineEvent.eventType == query.eventType)
        if query.projectId:
            conditions.append(RiskTimelineEvent.projectId == query.projectId)
        return conditions


def _event(
    risk: Risk,
    project_id: UUID,
    action_id: UUID | None,
    event_type: RiskTimelineEventType,
    title: str,
    description: str,
    from_value: str | None,
    to_value: str | None,
    identity: SessionIdentity,
    occurred_at: datetime,
) -> RiskTimelineEvent:
    return RiskTimelineEvent(
        projectId=project_id,
        riskId=risk.id,
        actionItemId=action_id,
        eventType=event_type,
        title=title,
        description=description,
        fromValue=from_value,
        toValue=to_value,
        actorUserId=UUID(identity.user.id),
        actorNameSource=identity.user.displayName,
        occurredAt=occurred_at,
    )


def _resolution_note(existing: str | None, reason: str) -> str:
    return "\n".join(
        value
        for value in [existing.strip() if existing else None, f"风险解除：{reason}"]  # noqa: RUF001
        if value
    )[:2000]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _item(row: tuple[Any, ...]) -> RiskItem:
    risk, project, department, category, reporter, _resolved = row
    return RiskItem(
        id=risk.id,
        projectId=project.id,
        projectName=project.name,
        projectExternalCode=project.externalCode,
        departmentName=department.name if department else None,
        projectOwnerName=project.deliveryOwnerName,
        title=risk.title,
        description=risk.description,
        evidence=risk.evidence,
        suggestion=risk.suggestion,
        level=risk.level,
        status=risk.status,
        category=RiskCategoryOption(id=category.id, code=category.code, name=category.name),
        sourceType=risk.sourceType,
        sourceLabel=_source_label(risk.sourceType.value),
        reporterName=reporter.displayName if reporter else risk.reporterNameSource,
        weekCode=risk.weekCode,
        actualCollectedAmountYuan=(
            f"{project.actualCollectedAmount:.2f}"
            if project.actualCollectedAmount is not None
            else None
        ),
        remainingAmountYuan=(
            f"{project.remainingAmount:.2f}" if project.remainingAmount is not None else None
        ),
        detectedAt=_iso(risk.detectedAt) or "",
        updatedAt=_iso(risk.updatedAt) or "",
    )


def _detail(row: tuple[Any, ...], same_project: Sequence[Any] = ()) -> RiskDetail:
    risk, _project, _department, _category, _reporter, resolved = row
    item = _item(row)
    return RiskDetail(
        **item.model_dump(),
        resolvedAt=_iso(risk.resolvedAt),
        resolvedByName=resolved.displayName if resolved else None,
        resolutionReason=risk.resolutionReason,
        sameProjectRisks=[
            SameProjectRisk(
                id=item[0], title=item[1], level=item[2], status=item[3], categoryName=item[4]
            )
            for item in same_project
        ],
    )


def _timeline_item(row: tuple[Any, ...]) -> TimelineItem:
    event, project, department, risk, category, reporter = row
    label, tone = event_presentation(event.eventType)
    return TimelineItem(
        id=event.id,
        eventType=event.eventType,
        eventLabel=label,
        tone=tone,
        projectId=project.id,
        projectName=project.name,
        departmentName=department.name if department else None,
        projectOwnerName=project.deliveryOwnerName,
        riskId=risk.id,
        riskTitle=risk.title,
        riskLevel=risk.level,
        riskStatus=risk.status,
        categoryName=category.name,
        title=event.title,
        description=event.description,
        fromValue=event.fromValue,
        toValue=event.toValue,
        actorName=event.actorNameSource or (reporter.displayName if reporter else "系统处理"),
        sourceLabel=_source_label(risk.sourceType.value),
        occurredAt=_iso(event.occurredAt) or "",
    )


def _source_label(source: str) -> str:
    return {
        "EXCEL": "项目清单 Excel",
        "LITIGATION": "发函诉讼清单",
        "MAIL_AI": "周报邮件 AI 提炼",
        "MANUAL": "日常上报",
    }.get(source, "其他来源")


RiskService = RisksService

__all__ = ["RiskCreate", "RiskService", "RisksService"]
