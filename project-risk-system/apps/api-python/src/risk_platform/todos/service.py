"""Transactional manager todo queries and risk-to-todo rules."""

from __future__ import annotations

from builtins import list as builtin_list
from datetime import UTC
from typing import Any, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.risks.models import Risk, RiskCategory
from risk_platform.shared.errors import ApiError
from risk_platform.todos.models import ActionItem, ActionItemSourceType, ActionItemStatus
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


class TodosService:
    """Own scoped todo reads and todo mutations; callers own the transaction boundary."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list(
        self, identity: SessionIdentity, query: ListTodosQuery
    ) -> ManagerTodoListResponse:
        async with self._session_factory() as session:
            rows = await self._rows(session, identity, query)
            scoped_rows = await self._rows(session, identity, ListTodosQuery())
        items = [self._item(row) for row in rows]
        all_items = [self._item(row) for row in scoped_rows]
        status_counts = {
            status: sum(item.status is status for item in all_items) for status in ActionItemStatus
        }
        owners = sorted({item.assigneeName for item in all_items}, key=lambda value: value)
        updated = max((item.updatedAt for item in all_items), default=None)
        return ManagerTodoListResponse(
            items=items,
            summary=ManagerTodoSummary(
                total=len(all_items),
                pending=status_counts[ActionItemStatus.PENDING],
                inProgress=status_counts[ActionItemStatus.IN_PROGRESS],
                completed=status_counts[ActionItemStatus.COMPLETED],
                emergency=sum(
                    item.urgency.value == "EMERGENCY"
                    and item.status is not ActionItemStatus.COMPLETED
                    for item in all_items
                ),
            ),
            owners=owners,
            schedule=build_schedule_suggestions(items),
            updatedAt=updated,
            dataScope=DataScopeType(identity.user.dataScope),
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
            row = await self._one(session, identity, todo_id, for_update=True)
            if row is None:
                raise ApiError(404, "NOT_FOUND", "待办事项不存在或已超出数据范围")
            todo, project, department, assignee, risk, category = row
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
            await session.flush()
            after = self._snapshot(todo)
            if risk is not None and before != after:
                session.add(_timeline_for_update(todo, project.id, risk.id, identity))
            await AuditService(session).record_success(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="TODO",
                action="ACTION_ITEM_UPDATED",
                resource_type="ACTION_ITEM",
                resource_id=str(todo.id),
                trace_id=trace_id,
                project_id=project.id,
            )
            if risk is not None:
                await invalidate_risk(session, risk.id)
        return self._detail((todo, project, department, assignee, risk, category))

    async def ensure_for_risk(
        self,
        session: AsyncSession,
        risk: Risk,
        *,
        owner_name: str | None,
        actor_id: UUID | None = None,
    ) -> ActionItem:
        """Return the sole auto todo for a risk, creating it when absent."""
        existing = await session.scalar(select(ActionItem).where(ActionItem.riskId == risk.id))
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
            projectId=risk.projectId,
            title=title,
            description=description,
            urgency=urgency_for_risk(risk.level),
            sourceType=ActionItemSourceType.RISK_SUGGESTION,
            assigneeNameSource=default_assignee_for_risk(risk.level, owner_name),
            createdById=actor_id,
        )
        session.add(todo)
        await session.flush()
        return todo

    async def _rows(
        self, session: AsyncSession, identity: SessionIdentity, query: ListTodosQuery
    ) -> builtin_list[tuple[Any, ...]]:
        conditions = [
            project_scope_predicate(UUID(identity.user.id), DataScopeType(identity.user.dataScope))
        ]
        if query.owner and query.owner.strip():
            owner = query.owner.strip()
            conditions.append(
                or_(ActionItem.assigneeNameSource == owner, User.displayName == owner)
            )
        if query.status is not None:
            conditions.append(ActionItem.status == query.status)
        result = await session.execute(
            self._statement(conditions).order_by(
                ActionItem.status.asc(),
                ActionItem.urgency.asc(),
                ActionItem.dueDate.asc(),
                ActionItem.updatedAt.desc(),
            )
        )
        return [cast(tuple[Any, ...], row) for row in result.all()]

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


__all__ = ["TodosService"]
