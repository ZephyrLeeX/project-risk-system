"""Provider-neutral, bounded read-only native tool-call loop for Agent V2."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from uuid import UUID, uuid4

from risk_platform.ai_providers.v2_adapter import (
    ProviderChatRequest,
    ProviderMessage,
    ProviderRole,
    ProviderToolCall,
    ProviderToolDefinition,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime
from risk_platform.auth.service import SessionIdentity
from risk_platform.model_types import JSONValue

from .schemas import AgentToolResult, CandidateRisk
from .scope import OUT_OF_SCOPE_MESSAGE, ScopeDecision, ScopePolicy
from .tools import AgentToolRegistry


class AgentLoopError(RuntimeError):
    """A safe, deterministic V2 core error; never a provider failover signal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProjectSelectionRequired(Exception):
    candidates: tuple[dict[str, JSONValue], ...]

    def __str__(self) -> str:
        return "project selection required"


@dataclass(frozen=True, slots=True)
class AgentLoopLimits:
    max_model_rounds: int = 6
    max_tool_calls: int = 16
    max_parallel_tool_calls: int = 4
    max_total_execution_time: float = 90.0
    max_single_tool_result: int = 48 * 1024
    max_total_tool_result: int = 96 * 1024
    max_context_size: int = 64 * 1024
    duplicate_call_threshold: int = 2


@dataclass(frozen=True, slots=True)
class AgentCoreOutcome:
    text: str
    out_of_scope: bool = False
    candidate_risks: tuple[CandidateRisk, ...] = ()


class ReadOnlyAgentCore:
    """Owns native messages only; DeepSeek fields and HTTP never enter this class."""

    def __init__(
        self,
        runtime: ProviderV2Runtime,
        tools: AgentToolRegistry,
        scope: ScopePolicy | None = None,
        limits: AgentLoopLimits | None = None,
        identity_loader: Callable[[SessionIdentity], Awaitable[SessionIdentity]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._tools = tools
        self._scope = scope or ScopePolicy()
        self._limits = limits or AgentLoopLimits()
        self._identity_loader = identity_loader

    async def run(
        self,
        identity: SessionIdentity,
        message: str,
        resume_context: str | None = None,
        *,
        conversation_id: UUID | None = None,
        execution_id: UUID | None = None,
    ) -> AgentCoreOutcome:
        if self._scope.decide(message) is ScopeDecision.OUT_OF_SCOPE:
            return AgentCoreOutcome(OUT_OF_SCOPE_MESSAGE, out_of_scope=True)
        started, calls, total = monotonic(), 0, 0
        repeated: dict[tuple[str, str], int] = {}
        messages: list[ProviderMessage] = [
            ProviderMessage(
                ProviderRole.SYSTEM,
                "你是项目风险管理助手。业务事实只能来自授权 tool 结果或用户明确陈述。"
                "需要写入时只能调用 proposal tool，不得直接执行业务写入，必须等待用户确认。"
                "风险上报 mutation guidance：当用户已明确表达要上报风险，且已能确定授权项目、"
                "有意义的风险标题和描述、以及一个有效 active 风险分类时，必须优先调用 "
                "risk_create_proposal，立即生成可编辑草稿，不得为了补齐信息而多轮追问。"
                "先使用 project_search/project_detail 和 risk_category_list 完成授权项目及分类 grounding，"
                "不要把 raw UUID 当作用户需要补充的信息。"
                "当 resume context 存在 server-provided selectedProjectId 时, 该项目已经由用户完成选择, "
                "并经过当前 DataScope revalidation; 后续需要 project_detail、risk_list 等项目精确查询时, "
                "必须直接使用 selectedProjectId, 不得再次通过原始模糊用户文本调用 project_search。"
                "金额、合同付款日、逾期天数、evidence、suggestion 都是可选信息；责任人和期望日期"
                "不是 RiskCreate 字段，绝不能作为创建风险的前置条件。level 可以给出 AI 建议值，"
                "但必须作为 draft 建议而不是系统事实。evidence 只能写用户明确陈述或授权工具事实，"
                "不得编造金额、日期、逾期天数或合同条款；缺失事实时可明确写“未提供”。suggestion "
                "可以生成处理建议，但必须表达为建议而非已发生事实。只有无法形成有效标题/描述、"
                "项目需要 PROJECT_SELECTION/MANUAL_INPUT、或找不到有效 active 分类时，才继续追问。"
                "本周处理建议 guidance：当用户请求本周处理建议、本周重点风险和建议、或本周应该优先处理什么时，"
                "必须先调用 weekly_report（未指定 weekStart 时使用当前周），不得直接生成泛化管理建议。"
                "若 riskCount 为 0，明确说明本周周报暂未识别到风险，不得编造风险；若有风险，按 HIGH、MEDIUM 优先，"
                "对周报中的风险项目调用 bounded 的 weekly_report_detail，必要时再调用 risk_list 和 todo_list。"
                "最终回答必须分成‘系统事实’与‘AI处理建议’，不得把建议写成已经发生的业务事实。",
            ),
            ProviderMessage(
                ProviderRole.USER,
                message if resume_context is None else f"{message}\n\n{resume_context}",
            ),
        ]
        definitions = tuple(
            ProviderToolDefinition(item["name"], item["description"], item["argumentsSchema"])  # type: ignore[arg-type]
            for item in self._tools.catalogue(identity)
        )
        # The candidate tuple is deliberately captured once per execution.
        # Provider-admin changes made while this loop is running must only
        # affect the next execution, never a later model round in this one.
        snapshot = await self._runtime.candidate_snapshot()
        for _ in range(self._limits.max_model_rounds):
            self._within_time(started)
            self._within_context(messages)
            request = ProviderChatRequest(tuple(messages), definitions)
            response = await self._runtime.chat_snapshot(snapshot, request)
            messages.append(
                ProviderMessage(
                    ProviderRole.ASSISTANT, response.content, tool_calls=response.tool_calls
                )
            )
            if not response.tool_calls:
                return AgentCoreOutcome(response.content or "当前系统数据中未找到")
            if len(response.tool_calls) > self._limits.max_parallel_tool_calls:
                raise AgentLoopError("AGENT_MAX_PARALLEL_TOOL_CALLS")
            for call in response.tool_calls:
                calls += 1
                if calls > self._limits.max_tool_calls:
                    raise AgentLoopError("AGENT_MAX_TOOL_CALLS")
                key = (call.name, self._canonical(call.arguments))
                repeated[key] = repeated.get(key, 0) + 1
                if repeated[key] > self._limits.duplicate_call_threshold:
                    raise AgentLoopError("AGENT_DUPLICATE_TOOL_CALL")
            results = await asyncio.gather(
                *[
                    self._invoke_current(
                        identity, call, conversation_id=conversation_id, execution_id=execution_id
                    )
                    for call in response.tool_calls
                ]
            )
            for call, result in zip(response.tool_calls, results, strict=True):
                if call.name == "project_search" and isinstance(result.data, dict):
                    raw_total = result.data.get("total", 0)
                    total_matches = (
                        int(raw_total) if isinstance(raw_total, (str, int, float)) else 0
                    )
                    items = result.data.get("items", [])
                    if total_matches > 1 and isinstance(items, list):
                        raise ProjectSelectionRequired(
                            tuple(item for item in items if isinstance(item, dict))
                        )
            for call, result in zip(response.tool_calls, results, strict=True):
                encoded = self._canonical(result.model_dump(mode="json")).encode()
                if len(encoded) > self._limits.max_single_tool_result:
                    raise AgentLoopError("AGENT_TOOL_RESULT_TOO_LARGE")
                total += len(encoded)
                if total > self._limits.max_total_tool_result:
                    raise AgentLoopError("AGENT_TOTAL_TOOL_RESULT_TOO_LARGE")
                messages.append(
                    ProviderMessage(ProviderRole.TOOL, encoded.decode(), tool_call_id=call.id)
                )
        raise AgentLoopError("AGENT_MAX_MODEL_ROUNDS")

    async def _invoke_current(
        self,
        original: SessionIdentity,
        call: ProviderToolCall,
        *,
        conversation_id: UUID | None = None,
        execution_id: UUID | None = None,
    ) -> AgentToolResult:
        identity = (
            await self._identity_loader(original) if self._identity_loader is not None else original
        )
        context = (
            (conversation_id, execution_id)
            if conversation_id is not None and execution_id is not None
            else None
        )
        if context is None:
            return await self._tools.invoke(
                identity, call.name, call.arguments, trace_id=str(uuid4())
            )
        return await self._tools.invoke(
            identity, call.name, call.arguments, trace_id=str(uuid4()), mutation_context=context
        )

    def _within_time(self, started: float) -> None:
        if monotonic() - started > self._limits.max_total_execution_time:
            raise AgentLoopError("AGENT_MAX_EXECUTION_TIME")

    def _within_context(self, messages: list[ProviderMessage]) -> None:
        encoded = self._canonical([message.content for message in messages]).encode()
        if len(encoded) > self._limits.max_context_size:
            raise AgentLoopError("AGENT_MAX_CONTEXT_SIZE")

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )


__all__ = [
    "AgentCoreOutcome",
    "AgentLoopError",
    "AgentLoopLimits",
    "ProjectSelectionRequired",
    "ReadOnlyAgentCore",
]
