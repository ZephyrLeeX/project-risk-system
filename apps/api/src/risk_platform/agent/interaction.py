"""Durable project-selection interactions and their resume boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
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
    AgentConversation,
    AgentEventType,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionAction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    AgentMessageRole,
    MutationDraft,
    MutationDraftStatus,
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
_PROJECT_SELECTION_CANCELLED_MESSAGE = (
    "项目选择已取消，上一问题尚未完成。你可以稍后说“继续上一个问题”重新处理。"
)
_PROJECT_SELECTION_EXPIRED_MESSAGE = (
    "项目选择已超时未处理，上一问题尚未完成。你可以稍后说“继续上一个问题”重新处理。"
)
# Machine-readable marker on the canned pairing messages so future tooling
# (summaries, retention, analytics) can identify them without text-matching.
_PROJECT_SELECTION_CANCELLED_NOTICE = "project_selection_cancelled"
_PROJECT_SELECTION_EXPIRED_NOTICE = "project_selection_expired"


async def _pair_interrupted_question(
    session: AsyncSession,
    conversation_id: UUID,
    user_message: AgentMessage,
    content: str,
    notice: str,
    now: datetime,
) -> None:
    """Append the deterministic assistant marker that pairs an interrupted turn.

    ``ConversationContextService._completed_turns`` drops a trailing USER
    message with no ASSISTANT reply, so without this marker an interrupted
    question vanishes from memory and a later “继续上一个问题” has no domain
    anchor to inherit. The message records no business fact and requires no
    model/provider call; the DB sequence trigger advances lastMessageSequence.
    """

    conversation = await session.scalar(
        select(AgentConversation)
        .where(AgentConversation.id == conversation_id)
        .with_for_update()
    )
    if conversation is None:
        raise ApiError(409, "AGENT_INTERACTION_CONTEXT_INVALID", "交互恢复上下文不可用")
    session.add(
        AgentMessage(
            conversationId=conversation.id,
            sequence=conversation.lastMessageSequence + 1,
            role=AgentMessageRole.ASSISTANT,
            content=content,
            structured={"systemNotice": cast(JSONValue, notice)},
            traceId=user_message.traceId,
            dataAsOf=now,
        )
    )
    await session.flush()


async def _expire_open_interaction(
    session: AsyncSession, interaction_id: UUID, owner_id: UUID, now: datetime
) -> bool:
    """Persistently expire a stale OPEN interaction and terminalize its execution.

    Returns True when this call performed the expiry. The caller must raise the
    410 OUTSIDE the transaction: raising inside would roll the status update
    back (the historical behaviour), leaving the interaction OPEN forever and
    the execution dangling in WAITING_FOR_USER — which blocks new sends
    (``AGENT_EXECUTION_ACTIVE``) with no remaining exit path, because the
    parked task is already terminal and ``request_cancellation`` cannot match
    it.
    """

    row = await session.scalar(
        select(AgentInteraction)
        .where(
            AgentInteraction.id == interaction_id,
            AgentInteraction.ownerUserId == owner_id,
        )
        .with_for_update()
    )
    if row is None or row.status is not AgentInteractionStatus.OPEN or row.expiresAt > now:
        return False
    row.status = AgentInteractionStatus.EXPIRED
    row.resolvedAt = now
    draft = await session.scalar(
        select(MutationDraft).where(MutationDraft.interactionId == row.id).with_for_update()
    )
    if draft is not None and draft.status is MutationDraftStatus.OPEN:
        draft.status = MutationDraftStatus.EXPIRED
        draft.resolvedAt = now
    execution = await session.scalar(
        select(AgentExecution)
        .where(AgentExecution.id == row.executionId)
        .with_for_update()
    )
    user_message: AgentMessage | None = None
    if execution is not None:
        if execution.status is AgentExecutionStatus.WAITING_FOR_USER:
            execution.status = AgentExecutionStatus.CANCELLED
            execution.completedAt = now
        user_message = await session.get(AgentMessage, execution.userMessageId)
    if user_message is not None and row.type is AgentInteractionType.PROJECT_SELECTION:
        await _pair_interrupted_question(
            session,
            row.conversationId,
            user_message,
            _PROJECT_SELECTION_EXPIRED_MESSAGE,
            _PROJECT_SELECTION_EXPIRED_NOTICE,
            now,
        )
    if user_message is not None and execution is not None:
        await append_event(
            session,
            conversation_id=row.conversationId,
            message_id=user_message.id,
            task_id=execution.taskId,
            event_type=AgentEventType.INTERACTION_RESOLVED,
            payload={
                "interactionId": str(row.id),
                "action": "EXPIRED",
                "traceId": user_message.traceId,
            },
        )
    return True


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
            # Persistently expire a stale OPEN confirmation BEFORE the main
            # transaction: MutationDraftService's in-session expiry guard
            # raises inside the caller's transaction, which would roll its
            # status update back and leave the execution dangling in
            # WAITING_FOR_USER (see _expire_open_interaction).
            expired_now = False
            async with self._sessions.begin() as expiry_session:
                expired_now = await _expire_open_interaction(
                    expiry_session, interaction_id, UUID(identity.user.id), datetime.now(UTC)
                )
            if expired_now:
                raise ApiError(410, "AGENT_INTERACTION_EXPIRED", "交互已过期")
            # Commit the draft AND terminalize the execution in ONE transaction.
            # ``MutationDraftService.respond`` runs its RBAC / already-resolved /
            # expiry / idempotency guards under the passed session (it locks the
            # interaction + draft with_for_update); once it returns, this same
            # transaction locks the execution, moves it to COMPLETED (CONFIRM) or
            # CANCELLED (CANCEL) with completedAt, and appends INTERACTION_RESOLVED
            # so a refresh sees a terminal execution instead of a dangling
            # WAITING_FOR_USER row. No new durable task is started — streamUrl
            # stays None and resumeAfterEventSequence stays 0 (the frontend
            # ignores both for a terminalized turn).
            now = datetime.now(UTC)
            async with self._sessions.begin() as session:
                await MutationDraftService(self._sessions).respond(
                    identity,
                    interaction_id,
                    payload.action,
                    payload.finalFields,
                    trace_id=UUID(trace_id),
                    session=session,
                )
                resolved_interaction = await session.scalar(
                    select(AgentInteraction).where(
                        AgentInteraction.id == interaction_id,
                        AgentInteraction.ownerUserId == UUID(identity.user.id),
                    )
                )
                if resolved_interaction is None:
                    raise ApiError(
                        404, "AGENT_INTERACTION_NOT_FOUND", "交互不存在或不属于当前用户"
                    )
                execution = await session.scalar(
                    select(AgentExecution)
                    .where(AgentExecution.id == resolved_interaction.executionId)
                    .with_for_update()
                )
                if (
                    execution is None
                    or execution.status is not AgentExecutionStatus.WAITING_FOR_USER
                ):
                    raise ApiError(
                        409, "AGENT_INTERACTION_NOT_WAITING", "Agent 当前不在等待状态"
                    )
                execution.status = (
                    AgentExecutionStatus.COMPLETED
                    if payload.action == "CONFIRM"
                    else AgentExecutionStatus.CANCELLED
                )
                execution.completedAt = now
                message = await session.get(AgentMessage, execution.userMessageId)
                if message is None:
                    raise ApiError(
                        409, "AGENT_INTERACTION_CONTEXT_INVALID", "交互恢复上下文不可用"
                    )
                await append_event(
                    session,
                    conversation_id=resolved_interaction.conversationId,
                    message_id=message.id,
                    task_id=execution.taskId,
                    event_type=AgentEventType.INTERACTION_RESOLVED,
                    payload={
                        "interactionId": str(resolved_interaction.id),
                        "action": payload.action,
                        "traceId": trace_id,
                    },
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
        expired_now = False
        async with self._sessions.begin() as expiry_session:
            expired_now = await _expire_open_interaction(
                expiry_session, interaction_id, owner_id, now
            )
        if expired_now:
            # Raise outside the transaction above so the persisted EXPIRED
            # status, the terminalized execution and the pairing marker commit.
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
                # Pair the interrupted USER question with a deterministic
                # assistant marker so a later “继续上一个问题” inherits a domain
                # anchor (see _pair_interrupted_question).
                await _pair_interrupted_question(
                    session,
                    row.conversationId,
                    message,
                    _PROJECT_SELECTION_CANCELLED_MESSAGE,
                    _PROJECT_SELECTION_CANCELLED_NOTICE,
                    now,
                )
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
                conversation = await session.scalar(
                    select(AgentConversation)
                    .where(AgentConversation.id == row.conversationId)
                    .with_for_update()
                )
                if conversation is None:
                    raise ApiError(
                        409, "AGENT_INTERACTION_CONTEXT_INVALID", "交互恢复上下文不可用"
                    )
                conversation.activeProjectId = UUID(str(selected["id"]))
                conversation.activeProjectName = str(selected["name"])
                conversation.contextUpdatedAt = now
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
            # Snapshot the durable event sequence in the SAME transaction that
            # enqueues the resumed task, before it commits and the task is
            # visible to the worker. The INTERACTION_RESOLVED event appended
            # above already advanced lastEventSequence; this scalar re-reads
            # the current value so the frontend opens the SSE stream with
            # ?afterSequence=<n> and replays the resumed execution's events
            # (the POST→SSE gap) instead of losing them.
            resume_after_sequence = await session.scalar(
                select(AgentConversation.lastEventSequence).where(
                    AgentConversation.id == row.conversationId
                )
            )
            return AgentInteractionRespondResponse(
                interaction=interaction_view(row),
                streamUrl=f"/api/agent/conversations/{row.conversationId}/events",
                resumeAfterEventSequence=int(resume_after_sequence or 0),
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
