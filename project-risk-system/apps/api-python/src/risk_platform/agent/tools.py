"""Closed Agent read-tool registry backed by existing domain services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from pydantic import BaseModel

from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.service import DashboardService
from risk_platform.model_types import JSONValue
from risk_platform.risks.schemas import RiskQuery
from risk_platform.risks.service import RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.todos.schemas import ListTodosQuery
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import WeeklyReportService, shanghai_week_start

from .schemas import AgentToolHelp, AgentToolResult

ToolCallable = Callable[[SessionIdentity, Mapping[str, object]], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    required_permissions: tuple[str, ...]
    supports_preview: bool
    call: ToolCallable


class AgentToolRegistry:
    """Expose only named, read-only domain service calls."""

    def __init__(
        self,
        dashboard: DashboardService,
        risks: RisksService,
        todos: TodosService,
        weekly_reports: WeeklyReportService,
    ) -> None:
        self._tools = (
            AgentTool(
                "dashboard_summary",
                "读取当前授权范围的风险看板汇总",
                ("dashboard.view",),
                False,
                lambda i, _: dashboard.summary(i),
            ),
            AgentTool(
                "dashboard_focus",
                "读取当前授权范围的重点风险",
                ("dashboard.view",),
                False,
                lambda i, _: dashboard.focus(i),
            ),
            AgentTool(
                "risk_list",
                "查询当前授权范围的风险列表",
                ("dashboard.view",),
                False,
                self._risk_list(risks),
            ),
            AgentTool(
                "risk_detail",
                "读取当前授权范围的风险详情",
                ("dashboard.view",),
                False,
                self._risk_detail(risks),
            ),
            AgentTool(
                "todo_list",
                "查询当前授权范围的管理者待办",
                ("dashboard.view",),
                False,
                self._todo_list(todos),
            ),
            AgentTool(
                "todo_detail",
                "读取当前授权范围的待办详情",
                ("dashboard.view",),
                False,
                self._todo_detail(todos),
            ),
            AgentTool(
                "weekly_report",
                "读取当前授权范围的周报汇总",
                ("dashboard.view",),
                False,
                self._weekly_report(weekly_reports),
            ),
            AgentTool(
                "weekly_report_detail",
                "读取当前授权项目的周报详情",
                ("dashboard.view",),
                False,
                self._weekly_detail(weekly_reports),
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
    ) -> AgentToolResult:
        tool = self._by_name.get(name)
        if tool is None:
            raise ApiError(400, "AGENT_TOOL_NOT_ALLOWED", "Agent 工具不在授权白名单内")
        if not set(tool.required_permissions).issubset(identity.user.permissions):
            raise ApiError(403, "FORBIDDEN", "当前账号无权使用此 Agent 工具")
        data = await tool.call(identity, arguments)
        if not isinstance(data, BaseModel):
            raise RuntimeError("Agent tool returned an unsupported result type")
        return AgentToolResult(
            tool=name,
            data=cast(JSONValue, data.model_dump(mode="json")),
            dataAsOf=datetime.now(UTC),
            traceId=trace_id,
        )

    @staticmethod
    def _risk_list(service: RisksService) -> ToolCallable:
        async def call(identity: SessionIdentity, arguments: Mapping[str, object]) -> object:
            query = RiskQuery(
                keyword=cast(str | None, arguments.get("keyword")),
                page=_int_argument(arguments.get("page"), 1),
                pageSize=_int_argument(arguments.get("pageSize"), 20),
            )
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
            query = ListTodosQuery(owner=cast(str | None, arguments.get("owner")))
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


__all__ = ["AgentTool", "AgentToolRegistry"]


def _int_argument(value: object | None, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
