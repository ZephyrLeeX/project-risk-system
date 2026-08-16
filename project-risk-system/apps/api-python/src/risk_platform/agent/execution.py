"""Restricted, non-mutating Agent Provider execution owned by T029."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Literal, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User, UserStatus
from risk_platform.auth.repository import AuthRepository
from risk_platform.auth.service import AuthService, SessionIdentity
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import get_scoped_project, project_scope_predicate
from risk_platform.reliability.core import TaskHandler, heartbeat
from risk_platform.reliability.dispatcher import DurableTaskCancelled, DurableTaskFailure
from risk_platform.reliability.models import DurableTask, DurableTaskStatus
from risk_platform.risks.models import ProjectRiskLevel, Risk, RiskCategory, RiskStatus
from risk_platform.shared.errors import ApiError
from risk_platform.todos.models import ActionItem

from .events import append_event, event_capacity_available
from .models import (
    AgentConfirmationOperation,
    AgentConfirmationToken,
    AgentEvent,
    AgentEventType,
    AgentExecutionConfig,
    AgentMessage,
    AgentMessageRole,
)
from .tools import AgentToolRegistry

PROTOCOL = "AGENT_PROVIDER_EXECUTION_V2"
INVALID_OUTPUT = "AGENT_PROVIDER_INVALID_OUTPUT"
CONFIG_INVALID = "AGENT_EXECUTION_CONFIG_INVALID"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
MAX_HISTORY_BYTES = 24 * 1024
MAX_TOOL_RESULT_BYTES = 48 * 1024
MAX_ACTION_TEXT_BYTES = 32 * 1024
MAX_CONTEXT_PROJECTS = 50
SYSTEM_INSTRUCTION = (
    "你是项目风险管理系统 Agent。用户询问当前项目、风险、待办或周报时,"
    "只能以 businessContext 和 toolResults 作为系统业务事实来源, 不得用模型自身世界知识替代。"
    "信息不足时必须调用工具; 不得虚构项目、风险、金额、负责人或状态。"
    "系统数据中没有答案时, 明确说明“当前系统数据中未找到”。回答优先使用项目名称而非仅 UUID。"
    "RESPOND 阶段必须依据 toolResults, 不得忽略工具结果重新按常识生成答案。"
)


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgressAction(_ProtocolModel):
    type: Literal["progress"]
    stage: Literal["analyzing", "querying", "drafting"]
    message: str = Field(min_length=1, max_length=256)


class ToolCallAction(_ProtocolModel):
    type: Literal["tool_call"]
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, JSONValue]


class TextAction(_ProtocolModel):
    type: Literal["text_delta"]
    text: str = Field(min_length=1, max_length=4096)


class PreviewContent(_ProtocolModel):
    operation: Literal["REPORT", "PROCESS", "RESOLVE"]
    projectId: UUID
    riskId: UUID | None
    todoId: UUID | None
    title: str = Field(max_length=250)
    description: str = Field(max_length=4000)
    riskLevel: Literal["HIGH", "MEDIUM", "LOW"] | None
    dueDate: date | None
    assigneeUserId: UUID | None
    categoryOptionId: str | None = Field(default=None, pattern=r"^C[1-9][0-9]*$")

    @model_validator(mode="after")
    def required_for_operation(self) -> PreviewContent:
        if self.operation == "REPORT":
            if (
                not self.title.strip()
                or not self.description.strip()
                or self.riskLevel is None
                or self.categoryOptionId is None
            ):
                raise ValueError("REPORT fields are incomplete")
            if any(
                value is not None
                for value in (self.riskId, self.todoId, self.dueDate, self.assigneeUserId)
            ):
                raise ValueError("REPORT contains fields outside its command contract")
        elif self.operation == "PROCESS":
            if self.riskId is None or self.todoId is None or not self.description.strip():
                raise ValueError("PROCESS fields are incomplete")
            if (
                self.title
                or self.riskLevel is not None
                or "categoryOptionId" in self.model_fields_set
            ):
                raise ValueError("PROCESS contains risk core fields")
        else:
            if self.riskId is None or not self.description.strip():
                raise ValueError("RESOLVE fields are incomplete")
            if any(
                value is not None
                for value in (self.todoId, self.riskLevel, self.dueDate, self.assigneeUserId)
            ) or self.title or "categoryOptionId" in self.model_fields_set:
                raise ValueError("RESOLVE contains fields outside its command contract")
        return self


class PreviewAction(_ProtocolModel):
    type: Literal["preview_proposal"]
    operation: Literal["REPORT", "PROCESS", "RESOLVE"]
    content: PreviewContent

    @model_validator(mode="after")
    def operation_matches(self) -> PreviewAction:
        if self.operation != self.content.operation:
            raise ValueError("preview operation mismatch")
        return self


class ProviderResponse(_ProtocolModel):
    protocol: Literal["AGENT_PROVIDER_EXECUTION_V2"]
    phase: Literal["PLAN", "RESPOND"]
    actions: list[dict[str, JSONValue]] = Field(max_length=64)


@dataclass(frozen=True, slots=True)
class ProviderTransportResponse:
    status_code: int
    body: bytes


class Provider(Protocol):
    async def __call__(
        self, config: AgentExecutionConfig, request: dict[str, JSONValue]
    ) -> ProviderTransportResponse: ...


class AgentProviderError(RuntimeError):
    """Safe transport classification supplied by a Provider adapter."""

    def __init__(
        self,
        *,
        status_code: int | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__("agent provider request failed")
        self.status_code, self.code, self._retryable = status_code, code, retryable

    @property
    def retryable(self) -> bool:
        if self._retryable is not None:
            return self._retryable
        return self.status_code is None or self.status_code in {408, 429} or bool(
            self.status_code and self.status_code >= 500
        )


class AgentProviderInvalidOutput(RuntimeError):
    pass


class AgentReportCategoryStale(RuntimeError):
    pass


class AgentBackpressure(RuntimeError):
    pass


class AgentToolResultTooLarge(RuntimeError):
    pass


class AgentCancellationAlreadyPersisted(DurableTaskCancelled):
    pass


class AgentExecutionWorker:
    """Execute at most PLAN + RESPOND using only injected restricted dependencies."""

    with_context = True

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: Provider,
        tools: AgentToolRegistry,
        *,
        heartbeat_interval: float = 15.0,
        attempt_timeout_seconds: float | None = None,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._tools = tools
        self._heartbeat_interval = heartbeat_interval
        self._attempt_timeout_seconds = attempt_timeout_seconds

    async def __call__(
        self, payload: Mapping[str, JSONValue], *, task_id: UUID, lease_token: UUID
    ) -> None:
        ids: tuple[UUID, UUID, UUID, UUID] | None = None
        started = asyncio.get_running_loop().time()
        try:
            ids = self._payload_ids(payload)
            config, _task, message, identity, history = await self._load_initial(
                ids, task_id, lease_token
            )
            existing = await self._existing_outcome(task_id)
            if existing == "completed":
                return
            if existing == "cancelled":
                raise AgentCancellationAlreadyPersisted
            if existing is not None:
                raise DurableTaskFailure(
                    existing, retryable=False, summary="persisted terminal agent outcome"
                )
            if config.cancellationRequestedAt is not None:
                await self._cancel(config.id, task_id, lease_token)
            if config.providerConfigId is None:
                await self._fail(
                    config.id,
                    task_id,
                    lease_token,
                    "AGENT_PROVIDER_UNAVAILABLE",
                    retryable=False,
                )
            business_context = await self._business_context(identity)
            plan_request = self._request(
                phase="PLAN",
                message=message.content,
                history=history,
                tools=cast(list[JSONValue], self._tools.catalogue(identity)),
                business_context=business_context,
            )
            plan_raw = await self._provider_round(
                config, task_id, lease_token, plan_request, started
            )
            plan = self._validate_response(plan_raw, "PLAN")
            await self._check_cancelled(config.id, task_id, lease_token)
            tool_results = await self._persist_plan_and_run_tools(
                config.id, task_id, lease_token, plan, identity, started
            )
            await self._check_cancelled(config.id, task_id, lease_token)
            categories = await self._category_options()
            respond_request = self._request(
                phase="RESPOND",
                message=message.content,
                history=history,
                tools=cast(list[JSONValue], self._tools.catalogue(identity)),
                tool_results=tool_results,
                categories=categories,
                business_context=business_context,
            )
            response_raw = await self._provider_round(
                config, task_id, lease_token, respond_request, started
            )
            response = self._validate_response(response_raw, "RESPOND")
            if (
                self._action_text_size(plan) + self._action_text_size(response)
                > MAX_ACTION_TEXT_BYTES
            ):
                raise AgentProviderInvalidOutput
            await self._check_cancelled(config.id, task_id, lease_token)
            await self._persist_response(config.id, task_id, lease_token, response, categories)
        except AgentCancellationAlreadyPersisted:
            raise
        except DurableTaskCancelled:
            assert ids is not None
            await self._terminal_event(
                ids[3], task_id, lease_token, "AGENT_EXECUTION_CANCELLED", False, force=True
            )
            raise
        except AgentBackpressure:
            assert ids is not None
            await self._terminal_event(
                ids[3], task_id, lease_token, "AGENT_STREAM_BACKPRESSURE", False, force=True
            )
            raise DurableTaskFailure(
                "AGENT_STREAM_BACKPRESSURE",
                retryable=False,
                summary="agent stream capacity reached",
            ) from None
        except AgentReportCategoryStale:
            assert ids is not None
            await self._terminal_event(
                ids[3], task_id, lease_token, "AGENT_REPORT_CATEGORY_STALE", True, force=True
            )
            raise DurableTaskFailure(
                "AGENT_REPORT_CATEGORY_STALE",
                retryable=True,
                summary="agent report category mapping changed before preview issue",
            ) from None
        except AgentProviderInvalidOutput:
            assert ids is not None
            await self._terminal_event(
                ids[3], task_id, lease_token, INVALID_OUTPUT, False, force=True
            )
            raise DurableTaskFailure(
                INVALID_OUTPUT, retryable=False, summary="invalid provider protocol"
            ) from None
        except AgentToolResultTooLarge:
            assert ids is not None
            await self._terminal_event(
                ids[3],
                task_id,
                lease_token,
                "AGENT_TOOL_RESULT_TOO_LARGE",
                False,
                force=True,
            )
            raise DurableTaskFailure(
                "AGENT_TOOL_RESULT_TOO_LARGE",
                retryable=False,
                summary="agent tool results exceed approved limit",
            ) from None
        except AgentProviderError as exc:
            assert ids is not None
            code = exc.code or (
                "AGENT_PROVIDER_UNAVAILABLE"
                if exc.retryable
                else "AGENT_PROVIDER_REQUEST_REJECTED"
            )
            await self._terminal_event(
                ids[3], task_id, lease_token, code, exc.retryable, force=True
            )
            raise DurableTaskFailure(
                code, retryable=exc.retryable, summary="provider request failed"
            ) from None
        except TimeoutError:
            assert ids is not None
            await self._terminal_event(
                ids[3],
                task_id,
                lease_token,
                "AGENT_PROVIDER_UNAVAILABLE",
                True,
                force=True,
            )
            raise DurableTaskFailure(
                "AGENT_PROVIDER_UNAVAILABLE", retryable=True, summary="provider timeout"
            ) from None
        except ApiError:
            assert ids is not None
            await self._terminal_event(
                ids[3], task_id, lease_token, INVALID_OUTPUT, False, force=True
            )
            raise DurableTaskFailure(
                INVALID_OUTPUT, retryable=False, summary="tool or preview validation failed"
            ) from None
        except DurableTaskFailure:
            raise
        except Exception as exc:
            # Configuration/payload/fencing failures are never retried or exposed verbatim.
            config_id = ids[3] if ids is not None else self._config_id_if_valid(payload)
            if config_id is not None:
                await self._try_config_invalid_event(config_id, task_id, lease_token)
            cause = type(getattr(exc, "orig", None)).__name__
            raise DurableTaskFailure(
                CONFIG_INVALID,
                retryable=False,
                summary=f"agent execution configuration invalid ({type(exc).__name__}:{cause})",
            ) from None

    async def _load_initial(
        self,
        ids: tuple[UUID, UUID, UUID, UUID],
        task_id: UUID,
        lease_token: UUID,
    ) -> tuple[
        AgentExecutionConfig,
        DurableTask,
        AgentMessage,
        SessionIdentity,
        list[dict[str, JSONValue]],
    ]:
        async with self._sessions.begin() as session:
            config, task = await self._locked_context(session, ids[3], task_id, lease_token)
            if (
                config.conversationId != ids[0]
                or config.userMessageId != ids[1]
                or config.requestedByUserId != ids[2]
            ):
                raise RuntimeError(CONFIG_INVALID)
            message = await session.get(AgentMessage, config.userMessageId)
            if message is None or message.role is not AgentMessageRole.USER:
                raise RuntimeError(CONFIG_INVALID)
            identity = await self._identity(session, config.requestedByUserId)
            rows = list(
                (
                    await session.scalars(
                        select(AgentMessage)
                        .where(AgentMessage.conversationId == config.conversationId)
                        .order_by(AgentMessage.sequence.desc())
                        .limit(12)
                    )
                ).all()
            )
            rows.reverse()
            history: list[dict[str, JSONValue]] = []
            size = 0
            for row in reversed(rows):
                item: dict[str, JSONValue] = {"role": row.role.value, "content": row.content}
                item_size = len(self._json_bytes(item))
                if size + item_size > MAX_HISTORY_BYTES:
                    continue
                history.insert(0, item)
                size += item_size
            return config, task, message, identity, history

    def _request(
        self,
        *,
        phase: Literal["PLAN", "RESPOND"],
        message: str,
        history: list[dict[str, JSONValue]],
        tools: list[JSONValue],
        tool_results: list[JSONValue] | None = None,
        categories: dict[str, RiskCategory] | None = None,
        business_context: dict[str, JSONValue] | None = None,
    ) -> dict[str, JSONValue]:
        request: dict[str, JSONValue] = {
            "protocol": PROTOCOL,
            "phase": phase,
            "message": message,
            "history": cast(list[JSONValue], history),
            "tools": tools,
            "systemInstruction": SYSTEM_INSTRUCTION,
        }
        if business_context is not None:
            request["businessContext"] = business_context
        if phase == "RESPOND":
            request["toolResults"] = tool_results or []
            request["riskCategoryOptions"] = {
                "schema": "RISK_CATEGORY_OPTIONS_V1",
                "items": [
                    {
                        "option_id": option_id,
                        "name": category.name,
                        "description": category.description,
                        "default_level": category.defaultLevel.value
                        if category.defaultLevel is not None
                        else None,
                    }
                    for option_id, category in (categories or {}).items()
                ],
            }
        if len(self._json_bytes(request)) > MAX_REQUEST_BYTES:
            raise AgentProviderInvalidOutput
        return request

    async def _business_context(self, identity: SessionIdentity) -> dict[str, JSONValue]:
        """Load a bounded, scope-filtered grounding summary for one execution."""

        scope = project_scope_predicate(
            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
        )
        async with self._sessions() as session:
            projects = list(
                (
                    await session.scalars(
                        select(Project)
                        .where(scope)
                        .order_by(Project.name, Project.id)
                        .limit(MAX_CONTEXT_PROJECTS)
                    )
                ).all()
            )
            risk_counts = await session.execute(
                select(Risk.level, func.count())
                .join(Project, Project.id == Risk.projectId)
                .where(scope, Risk.status == RiskStatus.ACTIVE)
                .group_by(Risk.level)
            )
            counts: dict[ProjectRiskLevel, int] = {
                level: int(count) for level, count in risk_counts.tuples().all()
            }
        return {
            "dataAsOf": datetime.now(UTC).isoformat(),
            "projects": [
                {"id": str(project.id), "name": project.name, "status": project.status.value}
                for project in projects
            ],
            "riskSummary": {
                "active": sum(int(value) for value in counts.values()),
                "high": int(counts.get(ProjectRiskLevel.HIGH, 0)),
                "medium": int(counts.get(ProjectRiskLevel.MEDIUM, 0)),
                "low": int(counts.get(ProjectRiskLevel.LOW, 0)),
            },
        }

    async def _provider_round(
        self,
        config: AgentExecutionConfig,
        task_id: UUID,
        lease_token: UUID,
        request: dict[str, JSONValue],
        started: float,
    ) -> dict[str, JSONValue]:
        raw = await self._await_with_heartbeat(
            self._provider(config, request), config, task_id, lease_token, started
        )
        return self._parse_transport(raw)

    @staticmethod
    def _parse_transport(raw: ProviderTransportResponse) -> dict[str, JSONValue]:
        if raw.status_code < 200 or raw.status_code >= 300:
            raise AgentProviderError(status_code=raw.status_code)
        if len(raw.body) > MAX_RESPONSE_BYTES:
            raise AgentProviderInvalidOutput
        try:
            decoded = raw.body.decode("utf-8")
            value = json.loads(decoded, object_pairs_hook=AgentExecutionWorker._unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise AgentProviderInvalidOutput from None
        if not isinstance(value, dict):
            raise AgentProviderInvalidOutput
        return cast(dict[str, JSONValue], value)

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON object key")
            value[key] = item
        return value

    async def _await_with_heartbeat[T](
        self,
        awaitable: Awaitable[T],
        config: AgentExecutionConfig,
        task_id: UUID,
        lease_token: UUID,
        started: float,
    ) -> T:
        call = asyncio.ensure_future(awaitable)
        deadline = started + (
            config.timeoutSeconds
            if self._attempt_timeout_seconds is None
            else self._attempt_timeout_seconds
        )
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                elapsed = asyncio.get_running_loop().time() - started
                until_heartbeat = self._heartbeat_interval - elapsed % self._heartbeat_interval
                done, _ = await asyncio.wait({call}, timeout=min(until_heartbeat, remaining))
                if done:
                    return await call
                async with self._sessions.begin() as session:
                    current, _ = await self._locked_context(
                        session, config.id, task_id, lease_token
                    )
                    if current.cancellationRequestedAt is not None:
                        raise DurableTaskCancelled
                    if not await heartbeat(session, task_id, lease_token):
                        raise RuntimeError(CONFIG_INVALID)
                    await self._append(
                        session, current, task_id, AgentEventType.HEARTBEAT, {}, force=False
                    )
        finally:
            if not call.done():
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)

    @staticmethod
    def _validate_response(
        raw: dict[str, JSONValue], phase: Literal["PLAN", "RESPOND"]
    ) -> list[ProgressAction | ToolCallAction | TextAction | PreviewAction]:
        try:
            envelope = ProviderResponse.model_validate(raw)
            if envelope.phase != phase:
                raise ValueError("phase mismatch")
            actions: list[ProgressAction | ToolCallAction | TextAction | PreviewAction] = []
            for value in envelope.actions:
                action_type = value.get("type")
                if action_type == "progress":
                    actions.append(ProgressAction.model_validate(value))
                elif phase == "PLAN" and action_type == "tool_call":
                    actions.append(ToolCallAction.model_validate(value))
                elif phase == "RESPOND" and action_type == "text_delta":
                    actions.append(TextAction.model_validate(value))
                elif phase == "RESPOND" and action_type == "preview_proposal":
                    actions.append(PreviewAction.model_validate(value))
                else:
                    raise ValueError("action is not permitted in this phase")
            if sum(isinstance(action, ToolCallAction) for action in actions) > 8:
                raise ValueError("too many tool calls")
            if sum(isinstance(action, PreviewAction) for action in actions) > 1:
                raise ValueError("too many previews")
            text_bytes = sum(
                len(action.message.encode())
                if isinstance(action, ProgressAction)
                else len(action.text.encode())
                if isinstance(action, TextAction)
                else 0
                for action in actions
            )
            if text_bytes > MAX_ACTION_TEXT_BYTES:
                raise ValueError("action text too large")
            return actions
        except (ValidationError, ValueError, TypeError, UnicodeError):
            raise AgentProviderInvalidOutput from None

    async def _persist_plan_and_run_tools(
        self,
        config_id: UUID,
        task_id: UUID,
        lease_token: UUID,
        actions: list[ProgressAction | ToolCallAction | TextAction | PreviewAction],
        identity: SessionIdentity,
        started: float,
    ) -> list[JSONValue]:
        progress = [action for action in actions if isinstance(action, ProgressAction)]
        async with self._sessions.begin() as session:
            config, _ = await self._locked_context(session, config_id, task_id, lease_token)
            if config.cancellationRequestedAt is not None:
                raise DurableTaskCancelled
            for action in progress:
                await self._append(
                    session,
                    config,
                    task_id,
                    AgentEventType.PROGRESS,
                    {"stage": action.stage, "message": action.message},
                )
        results: list[JSONValue] = []
        size = 0
        for index, candidate in enumerate(actions):
            if not isinstance(candidate, ToolCallAction):
                continue
            await self._check_cancelled(config_id, task_id, lease_token)
            async with self._sessions() as session:
                current_identity = await self._identity(session, UUID(identity.user.id))
            result = await self._await_with_heartbeat(
                self._tools.invoke(
                    current_identity,
                    candidate.name,
                    candidate.arguments,
                    trace_id=f"agent-tool:{task_id}:{index}",
                ),
                config,
                task_id,
                lease_token,
                started,
            )
            await self._check_cancelled(config_id, task_id, lease_token)
            item = cast(JSONValue, result.model_dump(mode="json"))
            size += len(self._json_bytes(item))
            if size > MAX_TOOL_RESULT_BYTES:
                raise AgentToolResultTooLarge
            results.append(item)
        return results

    async def _persist_response(
        self,
        config_id: UUID,
        task_id: UUID,
        lease_token: UUID,
        actions: list[ProgressAction | ToolCallAction | TextAction | PreviewAction],
        categories: dict[str, RiskCategory],
    ) -> None:
        async with self._sessions.begin() as session:
            config, _ = await self._locked_context(session, config_id, task_id, lease_token)
            if config.cancellationRequestedAt is not None:
                raise DurableTaskCancelled
            identity = await self._identity(session, config.requestedByUserId)
            previews = [action for action in actions if isinstance(action, PreviewAction)]
            for preview in previews:
                await self._validate_preview(session, identity, preview.content, categories)
            text = "".join(action.text for action in actions if isinstance(action, TextAction))
            assistant: AgentMessage | None = None
            if text:
                conversation = await session.scalar(
                    select(AgentMessage.sequence)
                    .where(AgentMessage.conversationId == config.conversationId)
                    .order_by(AgentMessage.sequence.desc())
                    .limit(1)
                )
                assistant = AgentMessage(
                    conversationId=config.conversationId,
                    sequence=(conversation or 0) + 1,
                    role=AgentMessageRole.ASSISTANT,
                    content=text,
                    traceId=f"agent-execution:{task_id}",
                    dataAsOf=datetime.now(UTC),
                )
                session.add(assistant)
                await session.flush()
            for action in actions:
                if isinstance(action, ProgressAction):
                    await self._append(
                        session,
                        config,
                        task_id,
                        AgentEventType.PROGRESS,
                        {"stage": action.stage, "message": action.message},
                    )
                elif isinstance(action, TextAction):
                    assert assistant is not None
                    await self._append(
                        session,
                        config,
                        task_id,
                        AgentEventType.MESSAGE_DELTA,
                        {"text": action.text},
                        message_id=assistant.id,
                    )
                elif isinstance(action, PreviewAction):
                    await self._issue_preview(
                        session, identity, config, task_id, action.content, categories
                    )
            await self._append(
                session,
                config,
                task_id,
                AgentEventType.COMPLETED,
                {"dataAsOf": self._timestamp(datetime.now(UTC))},
                message_id=assistant.id if assistant else config.userMessageId,
            )

    async def _validate_preview(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        content: PreviewContent,
        categories: dict[str, RiskCategory],
    ) -> None:
        permission = "risk.report" if content.operation == "REPORT" else "risk.resolve"
        if permission not in identity.user.permissions:
            raise AgentProviderInvalidOutput
        project = await get_scoped_project(
            session,
            content.projectId,
            UUID(identity.user.id),
            DataScopeType(identity.user.dataScope),
        )
        if project is None:
            raise AgentProviderInvalidOutput
        if content.operation == "REPORT":
            if content.categoryOptionId not in categories:
                raise AgentProviderInvalidOutput
            return
        risk = await session.get(Risk, content.riskId)
        if risk is None or risk.projectId != project.id or risk.status is not RiskStatus.ACTIVE:
            raise AgentProviderInvalidOutput
        if content.operation == "PROCESS":
            todo = await session.get(ActionItem, content.todoId)
            if todo is None or todo.projectId != project.id or todo.riskId != risk.id:
                raise AgentProviderInvalidOutput
            if content.assigneeUserId is not None:
                assignee = await session.get(User, content.assigneeUserId)
                if assignee is None or assignee.status is not UserStatus.ACTIVE:
                    raise AgentProviderInvalidOutput

    async def _issue_preview(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        config: AgentExecutionConfig,
        task_id: UUID,
        content: PreviewContent,
        categories: dict[str, RiskCategory],
    ) -> None:
        canonical_object = content.model_dump(mode="json")
        category = categories.get(content.categoryOptionId or "")
        if content.operation == "REPORT":
            if category is None:
                raise AgentProviderInvalidOutput
            locked = await session.scalar(
                select(RiskCategory)
                .where(RiskCategory.id == category.id, RiskCategory.isActive.is_(True))
                .with_for_update(read=True)
            )
            if locked is None:
                raise AgentReportCategoryStale
            projected_binding = self._category_binding(category)
            binding = self._category_binding(locked)
            if binding != projected_binding:
                raise AgentReportCategoryStale
            canonical_object.pop("categoryOptionId")
            canonical_object["categoryId"] = str(locked.id)
            canonical_object["categoryBindingDigest"] = binding
        else:
            canonical_object.pop("categoryOptionId")
            canonical_object["categoryId"] = None
            canonical_object["categoryBindingDigest"] = None
        canonical = self._canonical(canonical_object)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        scope = await self._scope_fact(session, identity)
        token = AgentConfirmationToken(
            tokenDigest=hashlib.sha256(raw_token.encode()).hexdigest(),
            ownerUserId=config.requestedByUserId,
            conversationId=config.conversationId,
            operation=AgentConfirmationOperation(content.operation),
            canonicalContent=canonical,
            contentDigest=digest,
            scopeDigest=hashlib.sha256(self._canonical(scope).encode()).hexdigest(),
            idempotencyKey=f"agent-preview:{task_id}:{digest}",
            issuedAt=now,
            expiresAt=now + timedelta(minutes=10),
        )
        session.add(token)
        await session.flush()
        await self._append(
            session,
            config,
            task_id,
            AgentEventType.PREVIEW,
            {
                "operation": content.operation,
                "content": cast(JSONValue, canonical_object),
                "contentDigest": digest,
                "confirmationToken": raw_token,
                "expiresAt": self._timestamp(token.expiresAt),
            },
        )

    async def _category_options(self) -> dict[str, RiskCategory]:
        async with self._sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(RiskCategory)
                        .where(RiskCategory.isActive.is_(True))
                        .order_by(RiskCategory.sortOrder, RiskCategory.code, RiskCategory.id)
                    )
                ).all()
            )
        return {f"C{index}": value for index, value in enumerate(rows, start=1)}

    @classmethod
    def _category_binding(cls, category: RiskCategory) -> str:
        value = {
            "categoryId": str(category.id),
            "updatedAt": cls._timestamp(category.updatedAt),
            "name": category.name,
            "description": category.description,
            "defaultLevel": category.defaultLevel.value if category.defaultLevel else None,
        }
        return hashlib.sha256(cls._canonical(value).encode()).hexdigest()

    async def _check_cancelled(
        self, config_id: UUID, task_id: UUID, lease_token: UUID
    ) -> None:
        async with self._sessions.begin() as session:
            config, _ = await self._locked_context(session, config_id, task_id, lease_token)
            if config.cancellationRequestedAt is not None:
                raise DurableTaskCancelled

    async def _cancel(self, config_id: UUID, task_id: UUID, lease_token: UUID) -> None:
        del config_id, task_id, lease_token
        raise DurableTaskCancelled

    async def _fail(
        self,
        config_id: UUID,
        task_id: UUID,
        lease_token: UUID,
        code: str,
        *,
        retryable: bool,
    ) -> None:
        await self._terminal_event(config_id, task_id, lease_token, code, retryable)
        raise DurableTaskFailure(code, retryable=retryable, summary="agent execution failed")

    async def _terminal_event(
        self,
        config_id: UUID,
        task_id: UUID,
        lease_token: UUID,
        code: str,
        retryable: bool,
        *,
        force: bool = False,
    ) -> None:
        async with self._sessions.begin() as session:
            config, _ = await self._locked_context(session, config_id, task_id, lease_token)
            await self._append(
                session,
                config,
                task_id,
                AgentEventType.ERROR,
                {
                    "code": code,
                    "message": {
                        INVALID_OUTPUT: "AI服务返回内容不符合Agent协议",
                        "AGENT_EXECUTION_CANCELLED": "Agent执行已取消",
                        "AGENT_STREAM_BACKPRESSURE": "Agent事件积压过多; 请重新读取会话",
                        "AGENT_PROVIDER_REQUEST_REJECTED": "AI服务拒绝了请求",
                    }.get(code, "AI服务暂时不可用"),
                    "retryable": retryable,
                },
                force=force,
            )

    async def _try_config_invalid_event(
        self, config_id: UUID, task_id: UUID, lease_token: UUID
    ) -> None:
        try:
            await self._terminal_event(
                config_id, task_id, lease_token, CONFIG_INVALID, False, force=True
            )
        except Exception:
            # A missing/mismatched config has no trustworthy conversation/message
            # destination. The dispatcher still records the fixed durable failure.
            return

    async def _append(
        self,
        session: AsyncSession,
        config: AgentExecutionConfig,
        task_id: UUID,
        event_type: AgentEventType,
        payload: Mapping[str, object],
        *,
        message_id: UUID | None = None,
        force: bool = False,
    ) -> None:
        trace_id = await session.scalar(
            select(AgentMessage.traceId).where(AgentMessage.id == config.userMessageId)
        )
        if trace_id is None:
            raise RuntimeError(CONFIG_INVALID)
        event_payload = {**payload, "traceId": trace_id}
        if not force and not await event_capacity_available(
            session, config.conversationId, event_payload
        ):
            raise AgentBackpressure
        await append_event(
            session,
            conversation_id=config.conversationId,
            message_id=message_id or config.userMessageId,
            task_id=task_id,
            event_type=event_type,
            payload=event_payload,
        )

    async def _existing_outcome(self, task_id: UUID) -> str | None:
        async with self._sessions() as session:
            event = await session.scalar(
                select(AgentEvent)
                .where(
                    AgentEvent.taskId == task_id,
                    AgentEvent.type.in_((AgentEventType.COMPLETED, AgentEventType.ERROR)),
                )
                .order_by(AgentEvent.sequence.desc())
                .limit(1)
            )
        if event is None:
            return None
        if event.type is AgentEventType.COMPLETED:
            return "completed"
        retryable = event.payload.get("retryable")
        code = event.payload.get("code")
        if retryable is False and isinstance(code, str):
            return "cancelled" if code == "AGENT_EXECUTION_CANCELLED" else code
        return None

    @staticmethod
    async def _locked_context(
        session: AsyncSession, config_id: UUID, task_id: UUID, lease_token: UUID
    ) -> tuple[AgentExecutionConfig, DurableTask]:
        task = await session.scalar(
            select(DurableTask).where(DurableTask.id == task_id).with_for_update()
        )
        config = await session.get(AgentExecutionConfig, config_id)
        if (
            task is None
            or config is None
            or config.taskId != task_id
            or task.status is not DurableTaskStatus.RUNNING
            or task.leaseToken != lease_token
        ):
            raise RuntimeError(CONFIG_INVALID)
        return config, task

    @staticmethod
    async def _identity(session: AsyncSession, user_id: UUID) -> SessionIdentity:
        repository = AuthRepository(session)
        user = await repository.user_by_id(user_id, for_update=False)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise RuntimeError(CONFIG_INVALID)
        access = await repository.user_access(user_id)
        return SessionIdentity(
            session_id=UUID(int=0),
            expires_at=datetime.max.replace(tzinfo=UTC),
            user=AuthService._authenticated_user(user, access),
        )

    @staticmethod
    async def _scope_fact(
        session: AsyncSession, identity: SessionIdentity
    ) -> dict[str, JSONValue]:
        project_ids = list(
            (
                await session.scalars(
                    select(Project.id)
                    .where(
                        project_scope_predicate(
                            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
                        )
                    )
                    .order_by(Project.id)
                )
            ).all()
        )
        return {
            "actorUserId": identity.user.id,
            "permissionCodes": cast(list[JSONValue], sorted(identity.user.permissions)),
            "projectScopeMode": identity.user.dataScope,
            "allowedProjectIds": cast(list[JSONValue], [str(value) for value in project_ids]),
        }

    @staticmethod
    def _payload_ids(payload: Mapping[str, JSONValue]) -> tuple[UUID, UUID, UUID, UUID]:
        expected = {
            "conversation_id",
            "user_message_id",
            "requested_by_user_id",
            "execution_configuration_id",
        }
        if set(payload) != expected:
            raise RuntimeError(CONFIG_INVALID)
        try:
            values = tuple(UUID(cast(str, payload[name])) for name in sorted(expected))
            by_name = dict(zip(sorted(expected), values, strict=True))
            return (
                by_name["conversation_id"],
                by_name["user_message_id"],
                by_name["requested_by_user_id"],
                by_name["execution_configuration_id"],
            )
        except (TypeError, ValueError):
            raise RuntimeError(CONFIG_INVALID) from None

    @staticmethod
    def _config_id_if_valid(payload: Mapping[str, JSONValue]) -> UUID | None:
        try:
            return UUID(cast(str, payload["execution_configuration_id"]))
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _json_bytes(cls, value: object) -> bytes:
        try:
            return cls._canonical(value).encode("utf-8")
        except (TypeError, UnicodeError):
            raise AgentProviderInvalidOutput from None

    @staticmethod
    def _action_text_size(
        actions: list[ProgressAction | ToolCallAction | TextAction | PreviewAction],
    ) -> int:
        return sum(
            len(action.message.encode())
            if isinstance(action, ProgressAction)
            else len(action.text.encode())
            if isinstance(action, TextAction)
            else 0
            for action in actions
        )

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def agent_execution_handlers(
    sessions: async_sessionmaker[AsyncSession],
    provider: Provider,
    tools: AgentToolRegistry,
) -> Mapping[str, TaskHandler]:
    """Return T029's module-local mapping; T040 owns production merging/registration."""

    worker = AgentExecutionWorker(sessions, provider, tools)
    return MappingProxyType({"AGENT_EXECUTION": cast(TaskHandler, worker)})


__all__ = [
    "AgentExecutionWorker",
    "AgentProviderError",
    "AgentProviderInvalidOutput",
    "Provider",
    "ProviderTransportResponse",
    "agent_execution_handlers",
]
