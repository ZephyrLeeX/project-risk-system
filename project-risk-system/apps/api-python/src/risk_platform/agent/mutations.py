"""Confirmed Agent mutations: proposal persistence is separate from commit authority."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User, UserStatus
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.projects.policy import ProjectStatusPolicy
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.risks.models import ProjectRiskLevel, Risk, RiskCategory, RiskSourceType
from risk_platform.risks.schemas import LifecycleRequest
from risk_platform.risks.service import RiskCreate, RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.todos.models import ActionItemStatus, ActionItemUrgency
from risk_platform.todos.service import TodoCreateCommand, TodosService

from .events import append_event
from .models import (
    AgentEventType,
    AgentExecution,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionAction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    MutationDraft,
    MutationDraftOperation,
    MutationDraftStatus,
)
from .schemas import MutationProposalRequest

TTL = timedelta(minutes=30)
_ALL_PROPOSAL_TOOLS = tuple(item.value for item in MutationDraftOperation)
_EDITABLE: dict[MutationDraftOperation, frozenset[str]] = {
    MutationDraftOperation.RISK_CREATE: frozenset(
        {"projectId", "title", "description", "level", "category", "evidence", "suggestion"}
    ),
    MutationDraftOperation.RISK_UPDATE: frozenset(
        {
            "riskId",
            "projectId",
            "title",
            "description",
            "level",
            "category",
            "evidence",
            "suggestion",
        }
    ),
    MutationDraftOperation.RISK_RESOLVE: frozenset({"riskId", "projectId", "resolutionReason"}),
    MutationDraftOperation.TODO_CREATE: frozenset(
        {"projectId", "riskId", "title", "description", "urgency", "assigneeUserId", "dueDate"}
    ),
    MutationDraftOperation.TODO_UPDATE: frozenset(
        {
            "projectId",
            "riskId",
            "todoId",
            "title",
            "description",
            "urgency",
            "status",
            "assigneeUserId",
            "dueDate",
            "completionNote",
        }
    ),
    MutationDraftOperation.PROJECT_STATUS_UPDATE: frozenset({"projectId", "targetStatus"}),
}


def proposal_tool_names() -> tuple[str, ...]:
    """The only write-shaped names that may be added to a model catalogue."""
    return _ALL_PROPOSAL_TOOLS


class MutationDraftService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def propose(
        self,
        identity: SessionIdentity,
        operation: MutationDraftOperation,
        payload: MutationProposalRequest,
        *,
        conversation_id: UUID,
        execution_id: UUID,
        trace_id: UUID,
        batch: list[MutationProposalRequest] | None = None,
    ) -> MutationDraft:
        if operation is MutationDraftOperation.PROJECT_STATUS_UPDATE:
            ProjectStatusPolicy.validate(ProjectStatus.DELIVERY, ProjectStatus.DELIVERY)
        if (
            operation is MutationDraftOperation.RISK_CREATE
            and "risk.report" not in identity.user.permissions
        ):
            raise ApiError(403, "FORBIDDEN", "当前账号无权上报风险")
        if (
            operation is not MutationDraftOperation.RISK_CREATE
            and "risk.resolve" not in identity.user.permissions
            and "risk.report" not in identity.user.permissions
        ):
            raise ApiError(403, "FORBIDDEN", "当前账号无权修改风险或待办")
        now = datetime.now(UTC)
        proposal = payload.model_dump(mode="json", exclude_none=True)
        if batch:
            proposal["items"] = [item.model_dump(mode="json", exclude_none=True) for item in batch]
        await self._prevalidate(identity, operation, proposal)
        digest = _digest(proposal)
        async with transaction(self._sessions) as session:
            execution = await session.scalar(
                select(AgentExecution).where(
                    AgentExecution.id == execution_id,
                    AgentExecution.conversationId == conversation_id,
                    AgentExecution.requestedByUserId == UUID(identity.user.id),
                )
            )
            if execution is None:
                raise ApiError(404, "AGENT_EXECUTION_NOT_FOUND", "Agent execution不存在")
            interaction = AgentInteraction(
                executionId=execution_id,
                conversationId=conversation_id,
                ownerUserId=UUID(identity.user.id),
                type=AgentInteractionType.WRITE_CONFIRMATION,
                status=AgentInteractionStatus.OPEN,
                candidateOptions=[],
                resumeContext={"mutation": True},
                expiresAt=now + TTL,
            )
            session.add(interaction)
            await session.flush()
            draft = MutationDraft(
                interactionId=interaction.id,
                ownerUserId=UUID(identity.user.id),
                conversationId=conversation_id,
                executionId=execution_id,
                operation=operation,
                status=MutationDraftStatus.OPEN,
                proposal=cast(dict[str, object], proposal),
                digest=digest,
                idempotencyKey=f"agent-draft:{interaction.id}",
                expiresAt=now + TTL,
            )
            session.add(draft)
            await session.flush()
            display_proposal = await _display_proposal(session, proposal)
            message = await session.get(AgentMessage, execution.userMessageId)
            if message is None:
                raise ApiError(409, "AGENT_INTERACTION_CONTEXT_INVALID", "写确认上下文不可用")
            execution.status = AgentExecutionStatus.WAITING_FOR_USER
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=message.id,
                task_id=execution.taskId,
                event_type=AgentEventType.INTERACTION_REQUIRED,
                payload={
                    "interactionId": str(interaction.id),
                    "type": AgentInteractionType.WRITE_CONFIRMATION.value,
                    "draftId": str(draft.id),
                    "operation": operation.value,
                    "draft": display_proposal,
                },
            )
            await AuditService(session).record_success(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="AGENT",
                action="AGENT_WRITE_PROPOSED",
                resource_type="AGENT_INTERACTION",
                resource_id=str(interaction.id),
                trace_id=trace_id,
                project_id=payload.projectId,
            )
            return draft

    async def respond(
        self,
        identity: SessionIdentity,
        interaction_id: UUID,
        action: str,
        final_fields: dict[str, JSONValue] | None,
        *,
        trace_id: UUID,
    ) -> dict[str, object]:
        async with transaction(self._sessions) as session:
            interaction = await session.scalar(
                select(AgentInteraction)
                .where(AgentInteraction.id == interaction_id)
                .with_for_update()
            )
            if interaction is None or interaction.ownerUserId != UUID(identity.user.id):
                raise ApiError(404, "AGENT_INTERACTION_NOT_FOUND", "交互不存在或不属于当前用户")
            draft = await session.scalar(
                select(MutationDraft)
                .where(MutationDraft.interactionId == interaction_id)
                .with_for_update()
            )
            if draft is None or interaction.type is not AgentInteractionType.WRITE_CONFIRMATION:
                raise ApiError(409, "AGENT_INTERACTION_CONTEXT_INVALID", "写确认上下文不可用")
            if (
                interaction.status is not AgentInteractionStatus.OPEN
                or draft.status is not MutationDraftStatus.OPEN
            ):
                raise ApiError(409, "AGENT_INTERACTION_ALREADY_RESOLVED", "交互已处理")
            if interaction.expiresAt <= datetime.now(UTC):
                interaction.status, draft.status = (
                    AgentInteractionStatus.EXPIRED,
                    MutationDraftStatus.EXPIRED,
                )
                raise ApiError(410, "AGENT_INTERACTION_EXPIRED", "交互已过期")
            if action == "CANCEL":
                interaction.status, interaction.responseAction = (
                    AgentInteractionStatus.CANCELLED,
                    AgentInteractionAction.CANCEL,
                )
                draft.status, draft.resolvedAt = MutationDraftStatus.CANCELLED, datetime.now(UTC)
                return {"status": "CANCELLED", "items": []}
            if action != "CONFIRM" or final_fields is None:
                raise ApiError(422, "VALIDATION_ERROR", "确认参数无效")
            proposal: dict[str, JSONValue] = dict(draft.proposal)
            if set(final_fields) - _EDITABLE[draft.operation]:
                raise ApiError(422, "VALIDATION_ERROR", "提交字段超出当前 mutation allowlist")
            proposal.update(final_fields)
            batch_items = proposal.get("items")
            if isinstance(batch_items, list):
                results: list[dict[str, object]] = []
                for index, raw_item in enumerate(batch_items):
                    if not isinstance(raw_item, dict):
                        results.append(
                            {
                                "draftId": draft.id,
                                "success": False,
                                "code": "VALIDATION_ERROR",
                            }
                        )
                        continue
                    item = raw_item
                    try:
                        await self._prevalidate(identity, draft.operation, item, session=session)
                        async with session.begin_nested():
                            item_result = await self._commit(
                                session,
                                identity,
                                draft,
                                item,
                                trace_id,
                                item_key=str(index),
                            )
                        results.append(
                            {
                                "draftId": draft.id,
                                "success": True,
                                "code": "OK",
                                **item_result,
                            }
                        )
                    except ApiError as error:
                        results.append({"draftId": draft.id, "success": False, "code": error.code})
                result = {
                    "resourceType": "RISK_BATCH",
                    "resourceId": draft.id,
                    "items": results,
                }
            else:
                await self._prevalidate(identity, draft.operation, proposal, session=session)
                result = await self._commit(session, identity, draft, proposal, trace_id)
            interaction.status, interaction.responseAction = (
                AgentInteractionStatus.RESOLVED,
                AgentInteractionAction.CONFIRM,
            )
            interaction.responsePayload = {
                "result": {
                    "resourceType": str(result["resourceType"]),
                    "resourceId": str(result["resourceId"]),
                }
            }
            interaction.resolvedAt = draft.resolvedAt = datetime.now(UTC)
            draft.status = MutationDraftStatus.CONFIRMED
            draft.resultResourceType = cast(str, result["resourceType"])
            draft.resultResourceId = cast(UUID, result["resourceId"])
            await AuditService(session).record_success(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="AGENT",
                action="AGENT_WRITE_CONFIRMED",
                resource_type=cast(str, result["resourceType"]),
                resource_id=str(result["resourceId"]),
                trace_id=trace_id,
                project_id=UUID(str(proposal["projectId"])),
            )
            return {
                "status": "CONFIRMED",
                "items": cast(list[dict[str, object]], result.get("items", []))
                or [{"draftId": draft.id, "success": True, "code": "OK", **result}],
            }

    async def _prevalidate(
        self,
        identity: SessionIdentity,
        operation: MutationDraftOperation,
        proposal: dict[str, JSONValue],
        *,
        session: AsyncSession | None = None,
    ) -> None:
        allowed = _EDITABLE[operation]
        if set(proposal) - allowed and set(proposal) - {"items"} - allowed:
            raise ApiError(422, "VALIDATION_ERROR", "proposal字段不在允许范围内")
        project_id = _uuid(proposal.get("projectId"))
        if project_id is None:
            raise ApiError(422, "VALIDATION_ERROR", "projectId不能为空")
        own_session = session is None
        if own_session:
            async with self._sessions() as opened:
                await self._prevalidate_session(opened, identity, operation, proposal, project_id)
        else:
            assert session is not None
            await self._prevalidate_session(session, identity, operation, proposal, project_id)

    async def _prevalidate_session(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        operation: MutationDraftOperation,
        proposal: dict[str, JSONValue],
        project_id: UUID,
    ) -> None:
        project = await session.scalar(
            select(Project).where(
                Project.id == project_id,
                project_scope_predicate(
                    UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                ),
            )
        )
        if project is None:
            raise ApiError(404, "NOT_FOUND", "项目不存在或已超出数据范围")
        if operation is MutationDraftOperation.RISK_CREATE and (
            not str(proposal.get("title", "")).strip()
            or not str(proposal.get("description", "")).strip()
        ):
            raise ApiError(422, "VALIDATION_ERROR", "风险标题和描述不能为空")
        if (
            operation is MutationDraftOperation.RISK_UPDATE
            and _uuid(proposal.get("riskId")) is None
        ):
            raise ApiError(422, "VALIDATION_ERROR", "riskId不能为空")
        if (
            operation is MutationDraftOperation.RISK_CREATE
            and _uuid(proposal.get("category")) is None
        ):
            raise ApiError(422, "VALIDATION_ERROR", "风险分类不能为空")
        if operation in {MutationDraftOperation.RISK_CREATE, MutationDraftOperation.RISK_UPDATE}:
            category = _uuid(proposal.get("category"))
            if (
                category is not None
                and await session.scalar(
                    select(RiskCategory).where(
                        RiskCategory.id == category, RiskCategory.isActive.is_(True)
                    )
                )
                is None
            ):
                raise ApiError(409, "RISK_CATEGORY_STALE", "风险分类已失效")

    async def _commit(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        draft: MutationDraft,
        proposal: dict[str, JSONValue],
        trace_id: UUID,
        item_key: str | None = None,
    ) -> dict[str, object]:
        operation = draft.operation
        project_id = UUID(str(proposal["projectId"]))
        if operation is MutationDraftOperation.RISK_CREATE:
            risk = await RisksService(self._sessions).create_in_session(
                session,
                RiskCreate(
                    project_id=project_id,
                    category_id=UUID(str(proposal["category"])),
                    title=str(proposal["title"]),
                    description=str(proposal["description"]),
                    level=ProjectRiskLevel(str(proposal.get("level", "MEDIUM"))),
                    source_type=RiskSourceType.AGENT,
                    dedupe_fingerprint=(
                        f"{draft.idempotencyKey}:{item_key}"
                        if item_key is not None
                        else draft.idempotencyKey
                    ),
                    evidence=cast(str | None, proposal.get("evidence")),
                    suggestion=cast(str | None, proposal.get("suggestion")),
                    reporter_user_id=UUID(identity.user.id),
                    actor_name=identity.user.displayName,
                ),
                actor_id=UUID(identity.user.id),
                trace_id=trace_id,
            )
            return {"resourceType": "RISK", "resourceId": risk.id}
        if operation is MutationDraftOperation.RISK_UPDATE:
            fields = {
                key: proposal[key]
                for key in _EDITABLE[operation]
                if key in proposal and key not in {"projectId", "riskId"}
            }
            risk = await RisksService(self._sessions).update_agent_in_session(
                session, identity, UUID(str(proposal["riskId"])), fields, trace_id=trace_id
            )
            return {"resourceType": "RISK", "resourceId": risk.id}
        if operation is MutationDraftOperation.RISK_RESOLVE:
            row = await RisksService(self._sessions).resolve_in_session(
                session,
                identity,
                UUID(str(proposal["riskId"])),
                LifecycleRequest(reason=str(proposal["resolutionReason"])),
                trace_id,
            )
            return {"resourceType": "RISK", "resourceId": cast(Risk, row[0]).id}
        if operation is MutationDraftOperation.TODO_CREATE:
            todo = await TodosService(self._sessions).create_for_risk_in_session(
                session,
                identity,
                TodoCreateCommand(
                    project_id=project_id,
                    risk_id=UUID(str(proposal["riskId"])),
                    title=str(proposal["title"]),
                    description=str(proposal["description"]),
                    urgency=ActionItemUrgency(str(proposal.get("urgency", "NORMAL"))),
                    due_date=_date(proposal.get("dueDate")),
                    assignee_user_id=_uuid(proposal.get("assigneeUserId")),
                    actor_id=UUID(identity.user.id),
                ),
                trace_id=trace_id,
            )
            return {"resourceType": "ACTION_ITEM", "resourceId": todo.id}
        if operation is MutationDraftOperation.TODO_UPDATE:
            todo_service = TodosService(self._sessions)
            todo_row = await todo_service._one(
                session, identity, UUID(str(proposal["todoId"])), for_update=True
            )
            if (
                todo_row is None
                or todo_row[0].riskId != _uuid(proposal.get("riskId"))
                or todo_row[0].projectId != project_id
            ):
                raise ApiError(404, "NOT_FOUND", "待办不存在或已超出数据范围")
            assert todo_row is not None
            todo, project, _department, _assignee, risk, _category = todo_row
            if risk is None:
                raise ApiError(409, "TODO_RISK_REQUIRED", "Agent待办必须绑定风险")
            before = todo_service._snapshot(todo)
            for key in ("title", "description", "completionNote"):
                if key in proposal:
                    setattr(todo, key, str(proposal[key]).strip())
            if "urgency" in proposal:
                todo.urgency = ActionItemUrgency(str(proposal["urgency"]))
            if "status" in proposal:
                todo.status = ActionItemStatus(str(proposal["status"]))
            if "dueDate" in proposal:
                todo.dueDate = _date(proposal["dueDate"])
            if "assigneeUserId" in proposal:
                user = await session.scalar(
                    select(User).where(
                        User.id == _uuid(proposal["assigneeUserId"]),
                        User.status == UserStatus.ACTIVE,
                    )
                )
                if user is None:
                    raise ApiError(404, "NOT_FOUND", "负责人不存在或不可用")
                todo.assigneeUserId, todo.assigneeNameSource = user.id, user.displayName
            await todo_service._finish_update(
                session, identity, todo, project.id, risk, before, trace_id
            )
            return {"resourceType": "ACTION_ITEM", "resourceId": todo.id}
        ProjectStatusPolicy.validate(ProjectStatus.DELIVERY, ProjectStatus.DELIVERY)
        raise AssertionError("unreachable")


class MutationConfirmationRequired(RuntimeError):
    """Stop the model loop after a draft has produced a durable interaction."""


async def _display_proposal(
    session: AsyncSession, proposal: dict[str, JSONValue]
) -> dict[str, JSONValue]:
    """Add non-authoritative labels/options for the confirmation form.

    These fields are never stored in or accepted by the mutation allowlist; the
    server reconstructs them from current database facts for display only.
    """
    displayed = dict(proposal)
    project_id = _uuid(proposal.get("projectId"))
    if project_id is not None:
        project = await session.get(Project, project_id)
        if project is not None:
            displayed["projectName"] = project.name
    categories = (
        await session.scalars(
            select(RiskCategory)
            .where(RiskCategory.isActive.is_(True))
            .order_by(RiskCategory.sortOrder, RiskCategory.name, RiskCategory.id)
        )
    ).all()
    displayed["categoryOptions"] = [
        {"id": str(item.id), "name": item.name} for item in categories
    ]
    category_id = _uuid(proposal.get("category"))
    selected = next((item for item in categories if item.id == category_id), None)
    if selected is not None:
        displayed["categoryName"] = selected.name
    return displayed


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value is not None else None
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "日期格式无效") from None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = ["MutationConfirmationRequired", "MutationDraftService", "proposal_tool_names"]
