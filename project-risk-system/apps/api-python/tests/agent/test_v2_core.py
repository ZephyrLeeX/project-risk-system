from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from risk_platform.agent.core import AgentLoopError, AgentLoopLimits, ReadOnlyAgentCore
from risk_platform.agent.schemas import AgentToolResult
from risk_platform.ai_providers.v2_adapter import (
    ProviderChatResponse,
    ProviderFinishReason,
    ProviderTokenUsage,
    ProviderToolCall,
)
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity


def _identity() -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(uuid4()), username="agent", displayName="Agent", departmentName=None,
            roleCodes=[], permissions=["agent.use", "dashboard.view"], dataScope="ALL",
            mustChangePassword=False,
        ),
    )


class _Runtime:
    def __init__(self, responses: list[ProviderChatResponse]) -> None:
        self.responses = responses
        self.calls = 0

    async def chat(self, _request: object) -> ProviderChatResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class _Tools:
    def __init__(self) -> None:
        self.invocations = 0

    def catalogue(self, _identity: SessionIdentity) -> list[dict[str, object]]:
        return [{"name": "risk_list", "description": "risks", "argumentsSchema": {}}]

    async def invoke(
        self, _identity: SessionIdentity, name: str, arguments: object, *, trace_id: str
    ) -> AgentToolResult:
        del arguments
        self.invocations += 1
        return AgentToolResult(
            toolInvocationId=trace_id, tool=name, data={"items": []}, dataAsOf=datetime.now(UTC),
            traceId=trace_id, provenance="test",
        )


def _response(*calls: ProviderToolCall, text: str | None = None) -> ProviderChatResponse:
    return ProviderChatResponse(
        content=text, tool_calls=calls,
        finish_reason=ProviderFinishReason.TOOL_CALLS if calls else ProviderFinishReason.STOP,
        usage=ProviderTokenUsage(1, 1, 2), latency_ms=1,
    )


def test_out_of_scope_never_calls_provider_or_tool() -> None:
    runtime, tools = _Runtime([]), _Tools()
    result = asyncio.run(ReadOnlyAgentCore(runtime, tools).run(_identity(), "帮我写 Python"))
    assert result.out_of_scope is True
    assert runtime.calls == tools.invocations == 0


def test_native_tool_call_is_correlated_then_finalized() -> None:
    runtime, tools = _Runtime([
        _response(ProviderToolCall("call-1", "risk_list", {})), _response(text="已完成汇总"),
    ]), _Tools()
    result = asyncio.run(ReadOnlyAgentCore(runtime, tools).run(_identity(), "项目风险有哪些"))
    assert result.text == "已完成汇总"
    assert runtime.calls == 2 and tools.invocations == 1


def test_duplicate_native_tool_call_terminates_before_third_invocation() -> None:
    call = ProviderToolCall("call-1", "risk_list", {})
    runtime, tools = _Runtime([_response(call), _response(call), _response(call)]), _Tools()
    with pytest.raises(AgentLoopError, match="AGENT_DUPLICATE_TOOL_CALL"):
        asyncio.run(ReadOnlyAgentCore(runtime, tools).run(_identity(), "项目风险有哪些"))
    assert tools.invocations == 2


def test_parallel_limit_is_hard_limit() -> None:
    runtime, tools = _Runtime([_response(
        ProviderToolCall("a", "risk_list", {}), ProviderToolCall("b", "risk_list", {})
    )]), _Tools()
    with pytest.raises(AgentLoopError, match="AGENT_MAX_PARALLEL_TOOL_CALLS"):
        asyncio.run(
            ReadOnlyAgentCore(
                runtime, tools, limits=AgentLoopLimits(max_parallel_tool_calls=1)
            ).run(_identity(), "项目风险有哪些")
        )
    assert tools.invocations == 0
