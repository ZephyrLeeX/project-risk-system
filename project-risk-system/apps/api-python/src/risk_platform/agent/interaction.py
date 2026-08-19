"""Durable project-selection interactions and their resume boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from typing import cast as type_cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.auth.service import SessionIdentity
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project
from risk_platform.projects.query_service import (
    ProjectQueryItem,
    ProjectSearchQuery,
    ProjectsQueryService,
)
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.shared.errors import ApiError

from .events import append_event
from .models import (
    AgentEventType,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionAction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    MutationDraft,
)
from .mutations import MutationDraftService, _display_proposal
from .schemas import (
    AgentInteractionRespondRequest,
    AgentInteractionRespondResponse,
    AgentInteractionResponse,
)

INTERACTION_TTL = timedelta(minutes=30)

_PROJECT_SELECTION_ACTIONS = frozenset({"SELECT", "MANUAL_INPUT", "CANCEL"})
_WRITE_CONFIRMATION_ACTIONS = frozenset({"CONFIRM", "CANCEL"})


def _validate_action(interaction_type: AgentInteractionType, action: str) -> None:
    allowed_actions = (
        _PROJECT_SELECTION_ACTIONS
        if interaction_type is AgentInteractionType.PROJECT_SELECTION
        else _WRITE_CONFIRMATION_ACTIONS
    )
    if action not in allowed_actions:
        raise ApiError(409, "AGENT_INTERACTION_ACTION_INVALID", "该交互不支持此操作")


def interaction_view(row: AgentInteraction) -> AgentInteractionResponse:
    return AgentInteractionResponse(
        id=row.id,
        type=row.type.value,
        status=row.status.value,
        conversationId=row.conversationId,
        executionId=row.executionId,
        candidates=cast(list[dict[str, JSONValue]], row.candidateOptions),
        expiresAt=row.expiresAt,
    )


class AgentInteractionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._projects = ProjectsQueryService(sessions)

    async def respond(
        self,
        identity: SessionIdentity,
        interaction_id: UUID,
        payload: AgentInteractionRespondRequest,
        trace_id: str,
    ) -> AgentInteractionRespondResponse:
        async with self._sessions() as dispatch_session:
            interaction_type = await dispatch_session.scalar(
                select(AgentInteraction.type).where(
                    AgentInteraction.id == interaction_id,
                    AgentInteraction.ownerUserId == UUID(identity.user.id),
                )
            )
        if interaction_type is None:
            raise ApiError(404, "AGENT_INTERACTION_NOT_FOUND", "交互不存在或不属于当前用户")
        _validate_action(interaction_type, payload.action)
        if interaction_type is AgentInteractionType.WRITE_CONFIRMATION and payload.action in {
            "CONFIRM",
            "CANCEL",
        }:
            await MutationDraftService(self._sessions).respond(
                identity,
                interaction_id,
                payload.action,
                payload.finalFields,
                trace_id=UUID(trace_id),
            )
            return AgentInteractionRespondResponse(
                interaction=await self._write_interaction_view(identity, interaction_id),
                streamUrl=None,
            )
        if interaction_type is AgentInteractionType.WRITE_CONFIRMATION:
            raise ApiError(409, "AGENT_INTERACTION_ACTION_INVALID", "该交互不支持此操作")
        owner_id = UUID(identity.user.id)
        if "dashboard.view" not in identity.user.permissions:
            raise ApiError(403, "FORBIDDEN", "当前账号无权重新解析项目")
        # Revalidate the supplied selection outside the consuming transaction,
        # then consume and enqueue atomically below. The transaction repeats the
        # ownership/status checks, so a concurrent response cannot win twice.
        selected: dict[str, object] | None = None
        manual_candidates: list[dict[str, object]] = []
        if payload.action == "SELECT":
            if payload.projectId is None:
                raise ApiError(409, "AGENT_INTERACTION_ACTION_INVALID", "该交互不支持此操作")
            item = await self._projects.detail(identity, payload.projectId)
            selected = self._candidate(item)
        elif payload.action == "MANUAL_INPUT":
            if payload.projectName is None:
                raise ApiError(409, "AGENT_INTERACTION_ACTION_INVALID", "该交互不支持此操作")
            result = await self._projects.search(
                identity, ProjectSearchQuery(keyword=payload.projectName, pageSize=20)
            )
            if result.total == 0:
                raise ApiError(404, "AGENT_PROJECT_NOT_FOUND", "当前系统中没有找到该项目")
            if result.total == 1:
                item = result.items[0]
                selected = self._candidate(item)
            else:
                manual_candidates = [
                    self._candidate(item)
                    for item in result.items
                ]

        now = datetime.now(UTC)
        async with self._sessions.begin() as expiry_session:
            expired = await expiry_session.execute(
                update(AgentInteraction)
                .where(
                    AgentInteraction.id == interaction_id,
                    AgentInteraction.status == AgentInteractionStatus.OPEN,
                    AgentInteraction.expiresAt <= now,
                )
                .values(status=AgentInteractionStatus.EXPIRED, resolvedAt=now)
            )
            if int(type_cast(CursorResult[object], expired).rowcount or 0) == 1:
                raise ApiError(410, "AGENT_INTERACTION_EXPIRED", "交互已过期")

        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(AgentInteraction)
                .where(AgentInteraction.id == interaction_id)
                .with_for_update()
            )
            if row is None or row.ownerUserId != owner_id:
                raise ApiError(404, "AGENT_INTERACTION_NOT_FOUND", "交互不存在或不属于当前用户")
            now = datetime.now(UTC)
            if row.status is AgentInteractionStatus.EXPIRED:
                raise ApiError(410, "AGENT_INTERACTION_EXPIRED", "交互已过期")
            if row.status is not AgentInteractionStatus.OPEN:
                raise ApiError(409, "AGENT_INTERACTION_ALREADY_RESOLVED", "交互已处理")
            execution = await session.scalar(
                select(AgentExecution).where(AgentExecution.id == row.executionId).with_for_update()
            )
            if execution is None or execution.status is not AgentExecutionStatus.WAITING_FOR_USER:
                raise ApiError(409, "AGENT_INTERACTION_NOT_WAITING", "Agent 当前不在等待状态")
            if payload.action == "SELECT" and not any(
                isinstance(item, dict) and item.get("id") == str(payload.projectId)
                for item in row.candidateOptions
            ):
                raise ApiError(422, "VALIDATION_ERROR", "所选项目不在当前候选中")
            if payload.action == "SELECT":
                current_scope = project_scope_predicate(
                    owner_id, DataScopeType(identity.user.dataScope)
                )
                still_authorized = await session.scalar(
                    select(Project.id).where(Project.id == payload.projectId, current_scope)
                )
                if still_authorized is None:
                    raise ApiError(422, "VALIDATION_ERROR", "所选项目已不在当前授权范围内")
            row.status = (
                AgentInteractionStatus.CANCELLED
                if payload.action == "CANCEL"
                else AgentInteractionStatus.RESOLVED
            )
            row.responseAction = AgentInteractionAction(payload.action)
            row.responsePayload = {
                "projectId": str(payload.projectId) if payload.projectId else None,
                "projectName": payload.projectName,
                "traceId": trace_id,
            }
            row.resolvedAt = now
            message = await session.get(AgentMessage, execution.userMessageId)
            if message is None:
                raise ApiError(409, "AGENT_INTERACTION_CONTEXT_INVALID", "交互恢复上下文不可用")
            await append_event(
                session,
                conversation_id=row.conversationId,
                message_id=message.id,
                task_id=execution.taskId,
                event_type=AgentEventType.INTERACTION_RESOLVED,
                payload={
                    "interactionId": str(row.id),
                    "action": payload.action,
                    "traceId": trace_id,
                },
            )
            if payload.action == "CANCEL":
                execution.status = AgentExecutionStatus.CANCELLED
                execution.completedAt = now
                return AgentInteractionRespondResponse(
                    interaction=interaction_view(row), streamUrl=None
                )
            context: dict[str, JSONValue]
            if manual_candidates:
                context = {"selectionCandidates": cast(list[JSONValue], manual_candidates)}
            else:
                if selected is None:
                    raise ApiError(409, "AGENT_INTERACTION_ACTION_INVALID", "该交互不支持此操作")
                context = {"selectedProject": cast(dict[str, JSONValue], selected)}
            config_id = uuid4()
            new_task = await enqueue_task(
                session,
                DurableTaskKind.AGENT_EXECUTION,
                f"agent-execution-resume:{execution.id}:{row.id}",
                {
                    "execution_id": str(execution.id),
                    "user_message_id": str(message.id),
                    "execution_configuration_id": str(config_id),
                },
            )
            execution.taskId = new_task.id
            execution.status = AgentExecutionStatus.RUNNING
            execution.resumeContext = context
            execution.updatedAt = now
            session.add(
                AgentExecutionConfig(
                    id=config_id,
                    taskId=new_task.id,
                    conversationId=execution.conversationId,
                    userMessageId=execution.userMessageId,
                    requestedByUserId=execution.requestedByUserId,
                    providerConfigId=None,
                    providerNameSnapshot=None,
                    endpointSnapshot=None,
                    protocolSnapshot=None,
                    modelSnapshot=None,
                    encryptedApiKeySnapshot=None,
                    timeoutSeconds=90,
                )
            )
            return AgentInteractionRespondResponse(
                interaction=interaction_view(row),
                streamUrl=f"/api/agent/conversations/{row.conversationId}/events",
            )

    async def _write_interaction_view(
        self, identity: SessionIdentity, interaction_id: UUID
    ) -> AgentInteractionResponse:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AgentInteraction).where(
                    AgentInteraction.id == interaction_id,
                    AgentInteraction.ownerUserId == UUID(identity.user.id),
                )
            )
            if row is None:
                raise ApiError(404, "AGENT_INTERACTION_NOT_FOUND", "交互不存在或不属于当前用户")
            draft = await session.scalar(
                select(MutationDraft).where(MutationDraft.interactionId == interaction_id)
            )
            view = interaction_view(row)
            view.draft = (
                None if draft is None else await _display_proposal(session, draft.proposal)
            )
            return view

    @staticmethod
    def _candidate(item: ProjectQueryItem) -> dict[str, object]:
        # ProjectQueryItem is the authorized ProjectsQueryService result; keep
        # this metadata bounded and never accept client-supplied fields.
        return {
            "id": str(item.id),
            "name": item.name,
            "externalCode": item.externalCode,
            "departmentName": item.departmentName,
            "status": item.status,
        }


__all__ = ["AgentInteractionService", "interaction_view"]
