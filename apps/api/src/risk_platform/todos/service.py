"""Transactional manager todo queries and risk-to-todo rules."""

from __future__ import annotations

from builtins import list as builtin_list
from dataclasses import dataclass
from datetime import UTC, date
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.risks.models import Risk, RiskCategory, RiskStatus
from risk_platform.shared.errors import ApiError
from risk_platform.todos.models import (
    ActionItem,
    ActionItemSourceType,
    ActionItemStatus,
    ActionItemUrgency,
)
from risk_platform.todos.policy import (
    build_schedule_suggestions,
    default_assignee_for_risk,
    urgency_for_risk,
)
from risk_platform.todos.schemas import (
    ListTodosQuery,
    ManagerTodoDetail,
    ManagerTodoItem,
    ManagerTodoListResponse,
    ManagerTodoSummary,
    TodoRiskResponse,
    UpdateTodoRequest,
)
from risk_platform.weekly_reports.service import invalidate_risk


@dataclass(frozen=True, slots=True)
class TodoProcessCommand:
    project_id: UUID
    risk_id: UUID
    description: str
    due_date: date | None = None
    assignee_user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TodoCreateCommand:
    project_id: UUID
    risk_id: UUID
    title: str
    description: str
    urgency: ActionItemUrgency = ActionItemUrgency.NORMAL
    due_date: date | None = None
    assignee_user_id: UUID | None = None
    actor_id: UUID | None = None


class TodosService:
    """Own scoped todo reads and todo mutations; callers own the transaction boundary."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list(
        self, identity: SessionIdentity, query: ListTodosQuery
    ) -> ManagerTodoListResponse:
        user_id = UUID(identity.user.id)
        data_scope = DataScopeType(identity.user.dataScope)
        scope_predicate = project_scope_predicate(user_id, data_scope)
        order = (
            ActionItem.status.asc(),
            ActionItem.urgency.asc(),
            ActionItem.dueDate.asc(),
            ActionItem.updatedAt.desc(),
            ActionItem.id.asc(),
        )
        async with self._session_factory() as session:
            filtered = self._filtered_conditions(query, scope_predicate)
            rows = (
                await session.execute(
                    self._statement(filtered)
                    .order_by(*order)
                    .offset((query.page - 1) * query.pageSize)
                    .limit(query.pageSize)
                )
            ).all()
            items = [self._item(cast(tuple[Any, ...], row)) for row in rows]
            total = (
                await session.scalar(
                    select(func.count(ActionItem.id))
                    .select_from(ActionItem)
                    .join(Project, Project.id == ActionItem.projectId)
                    .outerjoin(User, User.id == ActionItem.assigneeUserId)
                    .where(*filtered)
                )
                or 0
            )
            summary, updated = await self._scoped_summary(session, scope_predicate)
            owners = await self._scoped_owners(session, scope_predicate)
            schedule_items = await self._schedule_items(session, filtered, order)
        return ManagerTodoListResponse(
            items=items,
            page=query.page,
            pageSize=query.pageSize,
            total=total,
            summary=summary,
            owners=owners,
            schedule=build_schedule_suggestions(schedule_items),
            updatedAt=updated,
            dataScope=data_scope,
        )

    async def detail(self, identity: SessionIdentity, todo_id: UUID) -> ManagerTodoDetail:
        async with self._session_factory() as session:
            row = await self._one(session, identity, todo_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "待办事项不存在或已超出数据范围")
        return self._detail(row)

    async def update(
        self, identity: SessionIdentity, todo_id: UUID, payload: UpdateTodoRequest, trace_id: UUID
    ) -> ManagerTodoDetail:
        if not payload.model_fields_set:
            raise ApiError(400, "BAD_REQUEST", "请至少修改一项待办信息")
        if "assigneeName" in payload.model_fields_set and not payload.assigneeName:
            raise ApiError(400, "BAD_REQUEST", "负责人不能为空")
        async with transaction(self._session_factory) as session:
            row = await self.update_in_session(
                session, identity, todo_id, payload, trace_id=trace_id
            )
            todo, project, department, assignee, risk, category = row
        return self._detail((todo, project, department, assignee, risk, category))

    async def update_in_session(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        todo_id: UUID,
        payload: UpdateTodoRequest,
        *,
        trace_id: UUID,
    ) -> tuple[Any, ...]:
        """Apply the public todo update policy inside a caller-owned transaction."""
        if not payload.model_fields_set:
            raise ApiError(400, "BAD_REQUEST", "请至少修改一项待办信息")
        if "assigneeName" in payload.model_fields_set and not payload.assigneeName:
            raise ApiError(400, "BAD_REQUEST", "负责人不能为空")
        row = await self._one(session, identity, todo_id, for_update=True)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "待办事项不存在或已超出数据范围")
        todo, project, _department, _assignee, risk, _category = row
        before = self._snapshot(todo)
        if payload.status is not None:
            todo.status = payload.status
            if payload.status is ActionItemStatus.COMPLETED:
                todo.completedAt = _now()
                todo.completedById = UUID(identity.user.id)
            else:
                todo.completedAt = None
                todo.completedById = None
        if payload.assigneeName is not None:
            todo.assigneeNameSource = payload.assigneeName
            assignee_rows = (
                await session.scalars(
                    select(User)
                    .where(
                        User.displayName == payload.assigneeName,
                        User.status == UserStatus.ACTIVE,
                    )
                    .limit(2)
                )
            ).all()
            todo.assigneeUserId = assignee_rows[0].id if len(assignee_rows) == 1 else None
        if "dueDate" in payload.model_fields_set:
            todo.dueDate = payload.dueDate
        if "completionNote" in payload.model_fields_set:
            todo.completionNote = (
                payload.completionNote.strip() or None if payload.completionNote else None
            )
        await self._finish_update(session, identity, todo, project.id, risk, before, trace_id)
        return row

    async def create_for_risk_in_session(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        command: TodoCreateCommand,
        *,
        trace_id: UUID,
    ) -> ActionItem:
        """Create a non-default todo for an already existing, in-scope risk."""
        risk = await session.scalar(
            select(Risk)
            .join(Project, Project.id == Risk.projectId)
            .where(
                Risk.id == command.risk_id,
                Risk.projectId == command.project_id,
                project_scope_predicate(
                    UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                ),
            )
            .with_for_update()
        )
        if risk is None:
            raise ApiError(404, "NOT_FOUND", "风险不存在或已超出数据范围")
        if risk.status is not RiskStatus.ACTIVE:
            raise ApiError(409, "RISK_NOT_ACTIVE", "已解除风险不能新增待办")
        if not command.title.strip() or not command.description.strip():
            raise ApiError(422, "VALIDATION_ERROR", "待办标题和描述不能为空")
        assignee_name = None
        if command.assignee_user_id is not None:
            assignee = await session.scalar(
                select(User).where(
                    User.id == command.assignee_user_id, User.status == UserStatus.ACTIVE
                )
            )
            if assignee is None:
                raise ApiError(404, "NOT_FOUND", "负责人不存在或不可用")
            assignee_name = assignee.displayName
        todo = ActionItem(
            riskId=risk.id,
            isDefaultForRisk=False,
            projectId=risk.projectId,
            title=command.title.strip()[:250],
            description=command.description.strip(),
            urgency=command.urgency,
            sourceType=ActionItemSourceType.MANUAL,
            assigneeUserId=command.assignee_user_id,
            assigneeNameSource=assignee_name,
            dueDate=command.due_date,
            createdById=command.actor_id or UUID(identity.user.id),
        )
        session.add(todo)
        await session.flush()
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="TODO",
            action="ACTION_ITEM_CREATED",
            resource_type="ACTION_ITEM",
            resource_id=str(todo.id),
            trace_id=trace_id,
            project_id=todo.projectId,
        )
        return todo

    async def process_in_session(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        todo_id: UUID,
        command: TodoProcessCommand,
        *,
        trace_id: UUID,
    ) -> ActionItem:
        """Apply the approved Agent PROCESS command through todo domain policy."""
        if not command.description.strip():
            raise ApiError(400, "BAD_REQUEST", "处理说明不能为空")
        row = await self._one(session, identity, todo_id, for_update=True)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "待办事项不存在或已超出数据范围")
        todo, project, _department, _assignee, risk, _category = row
        if (
            risk is None
            or risk.id != command.risk_id
            or project.id != command.project_id
            or todo.projectId != command.project_id
            or risk.status is not RiskStatus.ACTIVE
        ):
            raise ApiError(404, "NOT_FOUND", "待办事项不存在或已超出数据范围")
        before = self._snapshot(todo)
        if command.due_date is not None:
            todo.dueDate = command.due_date
        if command.assignee_user_id is not None:
            assignee = await session.scalar(
                select(User).where(
                    User.id == command.assignee_user_id, User.status == UserStatus.ACTIVE
                )
            )
            if assignee is None:
                raise ApiError(404, "NOT_FOUND", "负责人不存在或不可用")
            todo.assigneeUserId = assignee.id
            todo.assigneeNameSource = assignee.displayName
        todo.completionNote = command.description.strip()
        await self._finish_update(session, identity, todo, project.id, risk, before, trace_id)
        return cast(ActionItem, todo)

    async def _finish_update(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        todo: ActionItem,
        project_id: UUID,
        risk: Risk | None,
        before: tuple[object, ...],
        trace_id: UUID,
    ) -> None:
        await session.flush()
        after = self._snapshot(todo)
        if risk is not None and before != after:
            session.add(_timeline_for_update(todo, project_id, risk.id, identity))
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="TODO",
            action="ACTION_ITEM_UPDATED",
            resource_type="ACTION_ITEM",
            resource_id=str(todo.id),
            trace_id=trace_id,
            project_id=project_id,
        )
        if risk is not None:
            await invalidate_risk(session, risk.id)

    async def ensure_for_risk(
        self,
        session: AsyncSession,
        risk: Risk,
        *,
        owner_name: str | None,
        actor_id: UUID | None = None,
    ) -> ActionItem:
        """Return the sole auto todo for a risk, creating it when absent."""
        existing = await session.scalar(
            select(ActionItem).where(
                ActionItem.riskId == risk.id, ActionItem.isDefaultForRisk.is_(True)
            )
        )
        title = f"{risk.title}处理事项"[:250]
        description = (
            risk.suggestion.strip()
            if risk.suggestion and risk.suggestion.strip()
            else risk.description
        )
        if existing is not None:
            existing.projectId = risk.projectId
            existing.title = title
            existing.description = description
            existing.urgency = urgency_for_risk(risk.level)
            existing.sourceType = ActionItemSourceType.RISK_SUGGESTION
            await session.flush()
            return existing
        todo = ActionItem(
            riskId=risk.id,
            isDefaultForRisk=True,
            projectId=risk.projectId,
            title=title,
            description=description,
            urgency=urgency_for_risk(risk.level),
            sourceType=ActionItemSourceType.RISK_SUGGESTION,
            assigneeNameSource=default_assignee_for_risk(risk.level, owner_name),
            createdById=actor_id,
        )
        session.add(todo)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(ActionItem).where(
                    ActionItem.riskId == risk.id, ActionItem.isDefaultForRisk.is_(True)
                )
            )
            if existing is None:
                raise
            return cast(ActionItem, existing)
        return todo

    @staticmethod
    def _filtered_conditions(query: ListTodosQuery, scope_predicate: Any) -> builtin_list[Any]:
        conditions: builtin_list[Any] = [scope_predicate]
        if query.owner and query.owner.strip():
            owner = query.owner.strip()
            conditions.append(
                or_(ActionItem.assigneeNameSource == owner, User.displayName == owner)
            )
        if query.status is not None:
            conditions.append(ActionItem.status == query.status)
        return conditions

    async def _scoped_summary(
        self, session: AsyncSession, scope_predicate: Any
    ) -> tuple[ManagerTodoSummary, str | None]:
        """Aggregate status counts and freshness over the full scoped set.

        Computed in a single SQL aggregate so the scoped summary is preserved
        without materializing every todo row.
        """
        row = (
            await session.execute(
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(ActionItem.status == ActionItemStatus.PENDING)
                    .label("pending"),
                    func.count()
                    .filter(ActionItem.status == ActionItemStatus.IN_PROGRESS)
                    .label("in_progress"),
                    func.count()
                    .filter(ActionItem.status == ActionItemStatus.COMPLETED)
                    .label("completed"),
                    func.count()
                    .filter(
                        ActionItem.urgency == ActionItemUrgency.EMERGENCY,
                        ActionItem.status != ActionItemStatus.COMPLETED,
                    )
                    .label("emergency"),
                    func.max(ActionItem.updatedAt).label("updated"),
                )
                .select_from(ActionItem)
                .join(Project, Project.id == ActionItem.projectId)
                .where(scope_predicate)
            )
        ).one()
        updated = row.updated.isoformat().replace("+00:00", "Z") if row.updated else None
        return (
            ManagerTodoSummary(
                total=row.total or 0,
                pending=row.pending or 0,
                inProgress=row.in_progress or 0,
                completed=row.completed or 0,
                emergency=row.emergency or 0,
            ),
            updated,
        )

    async def _scoped_owners(
        self, session: AsyncSession, scope_predicate: Any
    ) -> builtin_list[str]:
        """Distinct assignee names over the full scoped set, matching the
        ``assigneeName`` resolution used for items (assigned user display name
        falling back to the source name, then ``待分配``)."""
        expr = func.coalesce(User.displayName, ActionItem.assigneeNameSource, literal("待分配"))
        rows = (
            (
                await session.execute(
                    select(expr)
                    .distinct()
                    .select_from(ActionItem)
                    .join(Project, Project.id == ActionItem.projectId)
                    .outerjoin(User, User.id == ActionItem.assigneeUserId)
                    .where(scope_predicate)
                    .order_by(expr.asc())
                )
            )
            .scalars()
            .all()
        )
        return [str(value) for value in rows if value is not None]

    async def _schedule_items(
        self,
        session: AsyncSession,
        filtered: builtin_list[Any],
        order: tuple[Any, ...],
    ) -> builtin_list[ManagerTodoItem]:
        """Top active (non-completed) todos of the filtered set, bounded to the
        handful ``build_schedule_suggestions`` consumes."""
        rows = (
            await session.execute(
                self._statement(filtered)
                .where(ActionItem.status != ActionItemStatus.COMPLETED)
                .order_by(*order)
                .limit(5)
            )
        ).all()
        return [self._item(cast(tuple[Any, ...], row)) for row in rows]

    async def _one(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        todo_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[Any, ...] | None:
        statement = self._statement(
            [
                ActionItem.id == todo_id,
                project_scope_predicate(
                    UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                ),
            ]
        )
        if for_update:
            statement = statement.with_for_update(of=ActionItem)
        return cast(tuple[Any, ...] | None, (await session.execute(statement)).first())

    @staticmethod
    def _statement(conditions: builtin_list[Any]) -> Any:
        return (
            select(ActionItem, Project, Department, User, Risk, RiskCategory)
            .join(Project, Project.id == ActionItem.projectId)
            .outerjoin(Department, Department.id == Project.departmentId)
            .outerjoin(User, User.id == ActionItem.assigneeUserId)
            .outerjoin(Risk, Risk.id == ActionItem.riskId)
            .outerjoin(RiskCategory, RiskCategory.id == Risk.categoryId)
            .where(*conditions)
        )

    def _item(self, row: tuple[Any, ...]) -> ManagerTodoItem:
        todo, project, department, assignee, _risk, category = row
        return ManagerTodoItem(
            id=todo.id,
            riskId=todo.riskId,
            projectId=todo.projectId,
            projectName=project.name,
            projectOwnerName=project.deliveryOwnerName,
            departmentName=department.name if department else None,
            title=todo.title,
            description=todo.description,
            urgency=todo.urgency,
            status=todo.status,
            sourceType=todo.sourceType,
            typeLabel=category.name if category else "一般处理事项",
            assigneeUserId=todo.assigneeUserId,
            assigneeName=assignee.displayName if assignee else todo.assigneeNameSource or "待分配",
            dueDate=todo.dueDate,
            completionNote=todo.completionNote,
            completedAt=todo.completedAt.isoformat().replace("+00:00", "Z")
            if todo.completedAt
            else None,
            createdAt=todo.createdAt.isoformat().replace("+00:00", "Z"),
            updatedAt=todo.updatedAt.isoformat().replace("+00:00", "Z"),
        )

    def _detail(self, row: tuple[Any, ...]) -> ManagerTodoDetail:
        item = self._item(row)
        _todo, _project, _department, _assignee, risk, category = row
        risk_response = None
        if risk is not None:
            risk_response = TodoRiskResponse(
                id=risk.id,
                title=risk.title,
                description=risk.description,
                evidence=risk.evidence,
                suggestion=risk.suggestion,
                level=risk.level.value,
                status=risk.status.value,
                categoryName=category.name if category else "",
                sourceLabel=_source_label(risk.sourceType.value),
                detectedAt=risk.detectedAt.isoformat().replace("+00:00", "Z"),
            )
        return ManagerTodoDetail(**item.model_dump(), risk=risk_response)

    @staticmethod
    def _snapshot(todo: ActionItem) -> tuple[object, ...]:
        return (
            todo.status,
            todo.assigneeUserId,
            todo.assigneeNameSource,
            todo.dueDate,
            todo.completionNote,
        )


def _now() -> Any:
    from datetime import datetime

    return datetime.now(UTC)


def _timeline_for_update(
    todo: ActionItem, project_id: UUID, risk_id: UUID, identity: SessionIdentity
) -> Any:
    from risk_platform.timeline.models import RiskTimelineEvent, RiskTimelineEventType

    return RiskTimelineEvent(
        projectId=project_id,
        riskId=risk_id,
        actionItemId=todo.id,
        eventType=RiskTimelineEventType.ACTION_COMPLETED
        if todo.status is ActionItemStatus.COMPLETED
        else RiskTimelineEventType.ACTION_UPDATED,
        title="待办已完成" if todo.status is ActionItemStatus.COMPLETED else "待办已更新",
        description=todo.description,
        fromValue=None,
        toValue=todo.status.value,
        actorUserId=UUID(identity.user.id),
        actorNameSource=identity.user.displayName,
        metadata_={"status": todo.status.value},
    )


def _source_label(source: str) -> str:
    return {
        "EXCEL": "项目清单 Excel",
        "LITIGATION": "发函诉讼清单",
        "MAIL_AI": "周报邮件 AI 提炼",
        "MANUAL": "日常上报",
    }.get(source, "其他来源")


__all__ = ["TodoCreateCommand", "TodoProcessCommand", "TodosService"]
