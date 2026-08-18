"""Closed Agent read-tool registry backed by existing domain services."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.schemas import DashboardSummary
from risk_platform.dashboard.service import DashboardService
from risk_platform.model_types import JSONValue
from risk_platform.projects.query_service import ProjectSearchQuery, ProjectsQueryService
from risk_platform.risks.models import ProjectRiskLevel, RiskStatus
from risk_platform.risks.schemas import RiskDetail, RiskItem, RiskPage, RiskQuery
from risk_platform.risks.service import RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.todos.schemas import ListTodosQuery, ManagerTodoDetail, ManagerTodoListResponse
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.schemas import WeeklyProjectDetail, WeeklyReportResponse
from risk_platform.weekly_reports.service import WeeklyReportService, shanghai_week_start

from .models import MutationDraftOperation
from .mutations import (
    MutationConfirmationRequired,
    MutationDraftService,
    proposal_tool_names,
)
from .schemas import (
    AgentToolHelp,
    AgentToolResult,
    DashboardFocusToolResponse,
    EmptyToolArguments,
    MutationProposalRequest,
    MutationProposalResponse,
    ProjectDetailToolArguments,
    ProjectDetailToolResponse,
    ProjectListToolItem,
    ProjectListToolResponse,
    ProjectSearchToolArguments,
    RiskCategoryListToolResponse,
    RiskDetailToolArguments,
    RiskToolArguments,
    TodoDetailToolArguments,
    TodoToolArguments,
    WeeklyDetailToolArguments,
    WeeklyReportToolArguments,
)

ToolCallable = Callable[[SessionIdentity, Mapping[str, object]], Awaitable[object]]
ToolResponseAdapter = Callable[[object], BaseModel]
logger = logging.getLogger(__name__)


class AgentToolResultTypeError(RuntimeError):
    """A tool violated its closed, explicit Agent result contract."""


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    required_permissions: tuple[str, ...]
    supports_preview: bool
    request_model: type[BaseModel]
    response_adapter: ToolResponseAdapter
    call: ToolCallable


class AgentToolRegistry:
    """Expose only named, read-only domain service calls."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        dashboard: DashboardService,
        risks: RisksService,
        todos: TodosService,
        weekly_reports: WeeklyReportService,
    ) -> None:
        self._sessions = sessions
        self._tools = (
            AgentTool(
                "project_search",
                "读取当前授权范围内的项目名称、标识和状态",
                ("dashboard.view",),
                False,
                ProjectSearchToolArguments,
                _model_adapter(ProjectListToolResponse),
                self._project_search(ProjectsQueryService(sessions)),
            ),
            AgentTool(
                "project_detail",
                "读取当前授权范围内的项目详情",
                ("dashboard.view",),
                False,
                ProjectDetailToolArguments,
                _model_adapter(ProjectDetailToolResponse),
                self._project_detail(ProjectsQueryService(sessions)),
            ),
            AgentTool(
                "risk_category_list",
                "读取当前授权范围可用的风险分类",
                ("dashboard.view",),
                False,
                EmptyToolArguments,
                _model_adapter(RiskCategoryListToolResponse),
                self._risk_category_list(risks),
            ),
            AgentTool(
                "dashboard_summary",
                "读取当前授权范围的风险看板汇总",
                ("dashboard.view",),
                False,
                EmptyToolArguments,
                _model_adapter(DashboardSummary),
                lambda i, _: dashboard.summary(i),
            ),
            AgentTool(
                "dashboard_focus",
                "读取当前授权范围的重点风险",
                ("dashboard.view",),
                False,
                EmptyToolArguments,
                _dashboard_focus_adapter,
                lambda i, _: dashboard.focus(i),
            ),
            AgentTool(
                "risk_list",
                "查询当前授权范围的风险列表, 可用 projectId 精确关联项目",
                ("dashboard.view",),
                False,
                RiskToolArguments,
                _model_adapter(RiskPage),
                self._risk_list(risks),
            ),
            AgentTool(
                "risk_detail",
                "读取当前授权范围的风险详情",
                ("dashboard.view",),
                False,
                RiskDetailToolArguments,
                _model_adapter(RiskDetail),
                self._risk_detail(risks),
            ),
            AgentTool(
                "todo_list",
                "查询当前授权范围的管理者待办",
                ("dashboard.view",),
                False,
                TodoToolArguments,
                _model_adapter(ManagerTodoListResponse),
                self._todo_list(todos),
            ),
            AgentTool(
                "todo_detail",
                "读取当前授权范围的待办详情",
                ("dashboard.view",),
                False,
                TodoDetailToolArguments,
                _model_adapter(ManagerTodoDetail),
                self._todo_detail(todos),
            ),
            AgentTool(
                "weekly_report",
                "读取当前授权范围的周报汇总",
                ("dashboard.view",),
                False,
                WeeklyReportToolArguments,
                _model_adapter(WeeklyReportResponse),
                self._weekly_report(weekly_reports),
            ),
            AgentTool(
                "weekly_report_detail",
                "读取当前授权项目的周报详情",
                ("dashboard.view",),
                False,
                WeeklyDetailToolArguments,
                _model_adapter(WeeklyProjectDetail),
                self._weekly_detail(weekly_reports),
            ),
            *tuple(
                AgentTool(
                    name,
                    "生成待用户确认的 MutationDraft；risk_create 在项目、标题、描述和有效分类已明确时"
                    "应立即调用；不会直接写入业务表",
                    ("risk.report",),
                    True,
                    MutationProposalRequest,
                    _model_adapter(MutationProposalResponse),
                    self._proposal_only(name),
                )
                for name in proposal_tool_names()
            ),
        )
        self._by_name = {tool.name: tool for tool in self._tools}

    def help(self, identity: SessionIdentity) -> list[AgentToolHelp]:
        return [
            AgentToolHelp(
                name=tool.name,
                description=tool.description,
                requiredPermissions=list(tool.required_permissions),
                supportsPreview=tool.supports_preview,
            )
            for tool in self._tools
            if set(tool.required_permissions).issubset(identity.user.permissions)
        ]

    async def invoke(
        self,
        identity: SessionIdentity,
        name: str,
        arguments: Mapping[str, object],
        *,
        trace_id: str,
        mutation_context: tuple[UUID, UUID] | None = None,
    ) -> AgentToolResult:
        tool = self._by_name.get(name)
        if tool is None:
            raise ApiError(400, "AGENT_TOOL_NOT_ALLOWED", "Agent 工具不在授权白名单内")
        if not set(tool.required_permissions).issubset(identity.user.permissions):
            raise ApiError(403, "FORBIDDEN", "当前账号无权使用此 Agent 工具")
        try:
            validated = tool.request_model.model_validate(arguments)
        except ValidationError:
            raise ApiError(422, "VALIDATION_ERROR", "Agent 工具参数不符合约束") from None
        logger.info("agent tool invoke tool=%s status=start", name)
        if name in proposal_tool_names():
            if mutation_context is None:
                raise ApiError(
                    409,
                    "AGENT_MUTATION_CONTEXT_REQUIRED",
                    "proposal必须由服务端 execution context调用",
                )
            draft = await MutationDraftService(self._sessions).propose(
                identity,
                MutationDraftOperation(name),
                cast(MutationProposalRequest, validated),
                conversation_id=mutation_context[0],
                execution_id=mutation_context[1],
                trace_id=UUID(trace_id),
            )
            proposal_response = MutationProposalResponse(
                draftId=draft.id,
                interactionId=draft.interactionId,
                operation=draft.operation.value,
                status=draft.status.value,
                draft=draft.proposal,
            )
            del proposal_response
            raise MutationConfirmationRequired
        data: object | None = None
        completed_call = False
        try:
            data = await tool.call(identity, validated.model_dump(mode="python", exclude_none=True))
            completed_call = True
            response = tool.response_adapter(data)
        except Exception as error:
            logger.info(
                "agent tool invoke tool=%s result_type=%s status=failure error_class=%s",
                name,
                type(data).__name__ if completed_call else "unavailable",
                type(error).__name__,
            )
            raise
        logger.info(
            "agent tool invoke tool=%s result_type=%s status=success",
            name,
            type(data).__name__,
        )
        return AgentToolResult(
            toolInvocationId=trace_id,
            tool=name,
            data=cast(JSONValue, response.model_dump(mode="json")),
            dataAsOf=datetime.now(UTC),
            traceId=trace_id,
            provenance=f"agent-tool:{name}:{trace_id}",
        )

    def catalogue(self, identity: SessionIdentity) -> list[dict[str, object]]:
        """Return the closed Provider catalogue without callable internals."""

        return [
            {
                "name": tool.name,
                "description": tool.description,
                "argumentsSchema": tool.request_model.model_json_schema(),
            }
            for tool in self._tools
            if set(tool.required_permissions).issubset(identity.user.permissions)
        ]

    @staticmethod
    def _project_search(service: ProjectsQueryService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            try:
                query = ProjectSearchQuery.model_validate(arguments)
            except ValidationError:
                raise ApiError(422, "VALIDATION_ERROR", "Agent 工具参数不符合约束") from None
            result = await service.search(identity, query)
            return ProjectListToolResponse(
                items=[
                    ProjectListToolItem(id=item.id, name=item.name, status=item.status)
                    for item in result.items
                ],
                page=result.page,
                pageSize=result.pageSize,
                total=result.total,
            )

        return call

    @staticmethod
    def _project_detail(service: ProjectsQueryService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            item = await service.detail(identity, UUID(str(arguments["projectId"])))
            return ProjectDetailToolResponse(
                id=item.id, name=item.name, alias=item.alias, status=item.status
            )

        return call

    @staticmethod
    def _risk_category_list(service: RisksService) -> ToolCallable:
        async def call(identity: SessionIdentity, _: Mapping[str, object]) -> object:
            categories = await service.list_categories(identity)
            return RiskCategoryListToolResponse(
                items=[
                    {"id": str(item.id), "code": item.code, "name": item.name}
                    for item in categories
                ]
            )

        return call

    @staticmethod
    def _risk_list(service: RisksService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            query = RiskQuery(
                keyword=cast(str | None, arguments.get("keyword")),
                level=cast(ProjectRiskLevel | None, arguments.get("level")),
                status=cast(RiskStatus | None, arguments.get("status")),
                page=_int_argument(arguments.get("page"), 1),
                pageSize=_int_argument(arguments.get("pageSize"), 20),
            )
            project_id = cast(UUID | None, arguments.get("projectId"))
            if project_id is not None:
                return await service.list_for_project(identity, project_id, query)
            return await service.list(identity, query)

        return call

    @staticmethod
    def _risk_detail(service: RisksService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            return await service.detail(identity, UUID(str(arguments["riskId"])))

        return call

    @staticmethod
    def _todo_list(service: TodosService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            query = ListTodosQuery(
                owner=cast(str | None, arguments.get("owner")),
                page=_int_argument(arguments.get("page"), 1),
                pageSize=_int_argument(arguments.get("pageSize"), 20),
            )
            return await service.list(identity, query)

        return call

    @staticmethod
    def _todo_detail(service: TodosService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            return await service.detail(identity, UUID(str(arguments["todoId"])))

        return call

    @staticmethod
    def _weekly_report(service: WeeklyReportService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            value = arguments.get("weekStart")
            week = (
                shanghai_week_start(datetime.now(UTC))
                if value is None
                else datetime.fromisoformat(str(value)).date()
            )
            return await service.report(identity, week)

        return call

    @staticmethod
    def _weekly_detail(service: WeeklyReportService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            week = datetime.fromisoformat(str(arguments["weekStart"])).date()
            return await service.detail(identity, week, UUID(str(arguments["projectId"])))

        return call

    @staticmethod
    def _proposal_only(name: str) -> ToolCallable:
        async def call(_identity: SessionIdentity, _arguments: Mapping[str, object]) -> object:
            # The durable execution context is intentionally not part of model arguments.
            # Production commit/proposal orchestration injects it from the server-owned
            # interaction service; a direct model invocation fails closed.
            raise ApiError(
                409,
                "AGENT_MUTATION_CONTEXT_REQUIRED",
                f"{name}必须由服务端 execution context调用",
            )

        return call


__all__ = ["AgentTool", "AgentToolRegistry", "AgentToolResultTypeError"]


def _model_adapter(model: type[BaseModel]) -> ToolResponseAdapter:
    def adapt(value: object) -> BaseModel:
        if not isinstance(value, model):
            raise AgentToolResultTypeError(
                f"expected {model.__name__}, received {type(value).__name__}"
            )
        return value

    return adapt


def _dashboard_focus_adapter(value: object) -> DashboardFocusToolResponse:
    if not isinstance(value, list) or not all(isinstance(item, RiskItem) for item in value):
        raise AgentToolResultTypeError(f"expected list[RiskItem], received {type(value).__name__}")
    return DashboardFocusToolResponse(items=value)


def _int_argument(value: object | None, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
