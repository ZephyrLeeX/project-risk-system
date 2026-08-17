from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest

from risk_platform.agent.core import AgentLoopError, AgentLoopLimits, ReadOnlyAgentCore
from risk_platform.agent.schemas import AgentToolResult, CandidateRisk, CandidateRiskBasisType
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.ai_providers.v2_adapter import (
    ProviderCandidate,
    ProviderChatResponse,
    ProviderFinishReason,
    ProviderTokenUsage,
    ProviderToolCall,
    ProviderType,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity


def _identity() -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(uuid4()),
            username="agent",
            displayName="Agent",
            departmentName=None,
            roleCodes=[],
            permissions=["agent.use", "dashboard.view"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


class _Runtime:
    def __init__(self, responses: list[ProviderChatResponse]) -> None:
        self.responses = responses
        self.calls = 0

    async def candidate_snapshot(self) -> tuple[ProviderCandidate, ...]:
        return ()

    async def chat_snapshot(
        self, _snapshot: tuple[ProviderCandidate, ...], _request: object
    ) -> ProviderChatResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class _SnapshotRuntime(_Runtime):
    def __init__(self, responses: list[ProviderChatResponse]) -> None:
        super().__init__(responses)
        self.current_snapshot: tuple[ProviderCandidate, ...] = ()
        self.snapshots: list[tuple[ProviderCandidate, ...]] = []
        self.snapshot_change_after_first_round: tuple[ProviderCandidate, ...] | None = None

    async def candidate_snapshot(self) -> tuple[ProviderCandidate, ...]:
        return self.current_snapshot

    async def chat_snapshot(
        self, snapshot: tuple[ProviderCandidate, ...], request: object
    ) -> ProviderChatResponse:
        self.snapshots.append(snapshot)
        response = await super().chat_snapshot(snapshot, request)
        if self.calls == 1 and self.snapshot_change_after_first_round is not None:
            self.current_snapshot = self.snapshot_change_after_first_round
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
            toolInvocationId=trace_id,
            tool=name,
            data={"items": []},
            dataAsOf=datetime.now(UTC),
            traceId=trace_id,
            provenance="test",
        )


def _response(*calls: ProviderToolCall, text: str | None = None) -> ProviderChatResponse:
    return ProviderChatResponse(
        content=text,
        tool_calls=calls,
        finish_reason=ProviderFinishReason.TOOL_CALLS if calls else ProviderFinishReason.STOP,
        usage=ProviderTokenUsage(1, 1, 2),
        latency_ms=1,
    )


def _snapshot(model_name: str) -> tuple[ProviderCandidate, ...]:
    return (
        ProviderCandidate(
            account_id=uuid4(),
            account_name="test",
            provider_type=ProviderType.DEEPSEEK_OFFICIAL,
            model_config_id=uuid4(),
            model_name=model_name,
            timeout_seconds=30,
            encrypted_api_key="encrypted",
        ),
    )


def test_out_of_scope_never_calls_provider_or_tool() -> None:
    runtime, tools = _Runtime([]), _Tools()
    result = asyncio.run(
        ReadOnlyAgentCore(cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, tools)).run(
            _identity(), "帮我写 Python"
        )
    )
    assert result.out_of_scope is True
    assert runtime.calls == tools.invocations == 0


def test_native_tool_call_is_correlated_then_finalized() -> None:
    runtime, tools = (
        _Runtime(
            [
                _response(ProviderToolCall("call-1", "risk_list", {})),
                _response(text="已完成汇总"),
            ]
        ),
        _Tools(),
    )
    result = asyncio.run(
        ReadOnlyAgentCore(cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, tools)).run(
            _identity(), "项目风险有哪些"
        )
    )
    assert result.text == "已完成汇总"
    assert runtime.calls == 2 and tools.invocations == 1


def test_duplicate_native_tool_call_terminates_before_third_invocation() -> None:
    call = ProviderToolCall("call-1", "risk_list", {})
    runtime, tools = _Runtime([_response(call), _response(call), _response(call)]), _Tools()
    with pytest.raises(AgentLoopError, match="AGENT_DUPLICATE_TOOL_CALL"):
        asyncio.run(
            ReadOnlyAgentCore(cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, tools)).run(
                _identity(), "项目风险有哪些"
            )
        )
    assert tools.invocations == 2


def test_parallel_limit_is_hard_limit() -> None:
    runtime, tools = (
        _Runtime(
            [
                _response(
                    ProviderToolCall("a", "risk_list", {}), ProviderToolCall("b", "risk_list", {})
                )
            ]
        ),
        _Tools(),
    )
    with pytest.raises(AgentLoopError, match="AGENT_MAX_PARALLEL_TOOL_CALLS"):
        asyncio.run(
            ReadOnlyAgentCore(
                cast(ProviderV2Runtime, runtime),
                cast(AgentToolRegistry, tools),
                limits=AgentLoopLimits(max_parallel_tool_calls=1),
            ).run(_identity(), "项目风险有哪些")
        )
    assert tools.invocations == 0


def test_execution_reuses_one_immutable_provider_candidate_snapshot() -> None:
    runtime = _SnapshotRuntime(
        [_response(ProviderToolCall("call-1", "risk_list", {})), _response(text="完成")]
    )
    first, second = _snapshot("first"), _snapshot("second")
    runtime.current_snapshot = first
    tools = _Tools()

    runtime.snapshot_change_after_first_round = second
    asyncio.run(
        ReadOnlyAgentCore(cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, tools)).run(
            _identity(), "项目风险有哪些"
        )
    )
    assert runtime.snapshots == [first, first]


def test_next_execution_reads_a_fresh_provider_candidate_snapshot() -> None:
    runtime = _SnapshotRuntime([_response(text="第一次"), _response(text="第二次")])
    first, second = _snapshot("first"), _snapshot("second")
    runtime.current_snapshot = first
    asyncio.run(
        ReadOnlyAgentCore(cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, _Tools())).run(
            _identity(), "项目风险有哪些"
        )
    )
    runtime.current_snapshot = second
    asyncio.run(
        ReadOnlyAgentCore(cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, _Tools())).run(
            _identity(), "项目风险有哪些"
        )
    )
    assert runtime.snapshots == [first, second]


def test_candidate_risk_basis_contract_distinguishes_facts_and_ai_analysis() -> None:
    common = {
        "id": uuid4(),
        "projectId": uuid4(),
        "projectName": "项目 A",
        "title": "交付不确定性",
        "description": "需要进一步分析",
    }
    ai = CandidateRisk.model_validate({
        **common,
        "basisType": CandidateRiskBasisType.AI_ANALYSIS,
        "evidenceSummary": "AI风险分析: 基于问题描述推断, 需要进一步核实",
        "sourceInvocationIds": [],
    })
    assert ai.sourceInvocationIds == []
    fact = CandidateRisk.model_validate({
        **common,
        "basisType": CandidateRiskBasisType.SYSTEM_FACT,
        "evidenceSummary": "系统事实: 来自当前授权查询",
        "sourceInvocationIds": ["invocation-1"],
    })
    assert fact.basisType is CandidateRiskBasisType.SYSTEM_FACT
    with pytest.raises(ValueError, match="cannot cite"):
        CandidateRisk.model_validate({
            **common,
            "basisType": CandidateRiskBasisType.AI_ANALYSIS,
            "evidenceSummary": "AI风险分析: 推断",
            "sourceInvocationIds": ["invocation-1"],
        })
    with pytest.raises(ValueError, match="requires tool provenance"):
        CandidateRisk.model_validate({
            **common,
            "basisType": CandidateRiskBasisType.SYSTEM_FACT,
            "evidenceSummary": "系统事实: 无引用",
            "sourceInvocationIds": [],
        })
