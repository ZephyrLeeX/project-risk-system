"""Provider-neutral, bounded read-only native tool-call loop for Agent V2."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from risk_platform.ai_providers.v2_adapter import (
    ProviderChatRequest,
    ProviderMessage,
    ProviderRole,
    ProviderToolCall,
    ProviderToolDefinition,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime
from risk_platform.auth.service import SessionIdentity

from .schemas import AgentToolResult
from .scope import OUT_OF_SCOPE_MESSAGE, ScopeDecision, ScopePolicy
from .tools import AgentToolRegistry


class AgentLoopError(RuntimeError):
    """A safe, deterministic V2 core error; never a provider failover signal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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

    async def run(self, identity: SessionIdentity, message: str) -> AgentCoreOutcome:
        if self._scope.decide(message) is ScopeDecision.OUT_OF_SCOPE:
            return AgentCoreOutcome(OUT_OF_SCOPE_MESSAGE, out_of_scope=True)
        started, calls, total = monotonic(), 0, 0
        repeated: dict[tuple[str, str], int] = {}
        messages: list[ProviderMessage] = [
            ProviderMessage(
                ProviderRole.SYSTEM,
                "你是只读项目风险管理助手。业务事实只能来自 tool 结果, 不得提出写操作。",
            ),
            ProviderMessage(ProviderRole.USER, message),
        ]
        definitions = tuple(
            ProviderToolDefinition(item["name"], item["description"], item["argumentsSchema"])  # type: ignore[arg-type]
            for item in self._tools.catalogue(identity)
        )
        for _ in range(self._limits.max_model_rounds):
            self._within_time(started)
            self._within_context(messages)
            response = await self._runtime.chat(ProviderChatRequest(tuple(messages), definitions))
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
                *[self._invoke_current(identity, call) for call in response.tool_calls]
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
        self, original: SessionIdentity, call: ProviderToolCall
    ) -> AgentToolResult:
        identity = (
            await self._identity_loader(original)
            if self._identity_loader is not None
            else original
        )
        return await self._tools.invoke(identity, call.name, call.arguments, trace_id=str(uuid4()))

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


__all__ = ["AgentCoreOutcome", "AgentLoopError", "AgentLoopLimits", "ReadOnlyAgentCore"]
