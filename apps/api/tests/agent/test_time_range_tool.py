"""Agent risk_list time-range tool contract and prompt/scope regression tests.

Pins three layers of the relative-time risk query capability:

* ``RiskToolArguments`` — closed preset enum, explicit-window validation, and
  backward compatibility for the legacy argument shapes;
* ``AgentToolRegistry._risk_list`` — server-side preset resolution against an
  injected clock (the model never computes absolute dates);
* the Agent system instruction — time-range guidance steering 新增风险
  questions to ``risk_list`` with a preset while keeping 本周处理建议 on
  ``weekly_report`` — plus the builtin scope baseline for the six user phrasings.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from risk_platform.agent.core import ReadOnlyAgentCore
from risk_platform.agent.schemas import RiskToolArguments
from risk_platform.agent.scope import ScopeDecision, ScopePolicy
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.ai_providers.v2_adapter import (
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderFinishReason,
    ProviderTokenUsage,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.risks.models import ProjectRiskLevel
from risk_platform.risks.schemas import RiskPage
from risk_platform.risks.service import RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.shared.time_ranges import RiskTimeRangePreset

SHANGHAI = ZoneInfo("Asia/Shanghai")
PINNED_NOW = datetime(2026, 8, 21, 14, 0, 0, tzinfo=SHANGHAI)


def _identity() -> SessionIdentity:
    return SessionIdentity(
        session_id=UUID(int=1),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(UUID(int=2)),
            username="agent",
            displayName="Agent",
            departmentName=None,
            roleCodes=[],
            permissions=["agent.use", "dashboard.view"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


def test_legacy_arguments_stay_valid() -> None:
    arguments = RiskToolArguments.model_validate({"level": "HIGH"})
    assert arguments.level is ProjectRiskLevel.HIGH
    assert arguments.timeRange is None
    assert arguments.detectedFrom is None
    assert arguments.detectedTo is None


def test_preset_and_explicit_window_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="互斥"):
        RiskToolArguments.model_validate(
            {
                "timeRange": "CURRENT_WEEK",
                "detectedFrom": "2026-08-17T00:00:00+08:00",
                "detectedTo": "2026-08-24T00:00:00+08:00",
            }
        )


def test_explicit_window_requires_both_bounds_aware_and_ordered() -> None:
    with pytest.raises(ValidationError, match="同时提供"):
        RiskToolArguments.model_validate({"detectedFrom": "2026-08-17T00:00:00+08:00"})
    with pytest.raises(ValidationError, match="时区"):
        RiskToolArguments.model_validate(
            {
                "detectedFrom": "2026-08-17T00:00:00",
                "detectedTo": "2026-08-24T00:00:00",
            }
        )
    with pytest.raises(ValidationError, match="早于"):
        RiskToolArguments.model_validate(
            {
                "detectedFrom": "2026-08-24T00:00:00+08:00",
                "detectedTo": "2026-08-17T00:00:00+08:00",
            }
        )
    arguments = RiskToolArguments.model_validate(
        {
            "detectedFrom": "2026-08-17T00:00:00+08:00",
            "detectedTo": "2026-08-24T00:00:00+08:00",
        }
    )
    assert arguments.detectedFrom is not None
    assert arguments.detectedTo is not None


def test_unknown_preset_is_rejected_by_the_closed_enum() -> None:
    with pytest.raises(ValidationError):
        RiskToolArguments.model_validate({"timeRange": "NEXT_WEEK"})


# ---------------------------------------------------------------------------
# Tool-side preset resolution (injected clock, never model-computed dates)
# ---------------------------------------------------------------------------


class _CapturingRisks:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def list(
        self,
        _identity: SessionIdentity,
        query: object,
        **kwargs: object,
    ) -> RiskPage:
        self.calls.append({"query": query, **kwargs})
        return RiskPage(items=[], page=1, pageSize=10, total=0)

    async def list_for_project(
        self,
        _identity: SessionIdentity,
        _project_id: UUID,
        query: object,
        **kwargs: object,
    ) -> RiskPage:
        self.calls.append({"query": query, **kwargs})
        return RiskPage(items=[], page=1, pageSize=10, total=0)


def _registry(risks: _CapturingRisks) -> AgentToolRegistry:
    return AgentToolRegistry(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        cast(RisksService, risks),
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        clock=lambda: PINNED_NOW,
    )


def test_preset_resolves_server_side_against_the_injected_clock() -> None:
    risks = _CapturingRisks()
    asyncio.run(
        _registry(risks).invoke(
            _identity(),
            "risk_list",
            {"level": "HIGH", "timeRange": "CURRENT_WEEK"},
            trace_id="trace",
        )
    )
    assert len(risks.calls) == 1
    call = risks.calls[0]
    assert call["detected_from"] == datetime(2026, 8, 17, tzinfo=SHANGHAI)
    assert call["detected_to"] == datetime(2026, 8, 24, tzinfo=SHANGHAI)
    query = call["query"]
    assert getattr(query, "level", None) is ProjectRiskLevel.HIGH


@pytest.mark.parametrize(
    ("preset", "start", "end"),
    (
        (RiskTimeRangePreset.PREVIOUS_WEEK, (2026, 8, 10), (2026, 8, 17)),
        (RiskTimeRangePreset.LAST_7_DAYS, (2026, 8, 15), (2026, 8, 22)),
        (RiskTimeRangePreset.CURRENT_MONTH, (2026, 8, 1), (2026, 9, 1)),
        (RiskTimeRangePreset.PREVIOUS_MONTH, (2026, 7, 1), (2026, 8, 1)),
    ),
)
def test_every_preset_resolves_to_its_fixed_window(
    preset: RiskTimeRangePreset, start: tuple[int, int, int], end: tuple[int, int, int]
) -> None:
    risks = _CapturingRisks()
    asyncio.run(
        _registry(risks).invoke(
            _identity(), "risk_list", {"timeRange": preset.value}, trace_id="trace"
        )
    )
    call = risks.calls[0]
    assert call["detected_from"] == datetime(*start, tzinfo=SHANGHAI)
    assert call["detected_to"] == datetime(*end, tzinfo=SHANGHAI)


def test_explicit_window_passes_through_verbatim() -> None:
    risks = _CapturingRisks()
    explicit_from = datetime(2026, 8, 17, tzinfo=SHANGHAI)
    explicit_to = datetime(2026, 8, 24, tzinfo=SHANGHAI)
    asyncio.run(
        _registry(risks).invoke(
            _identity(),
            "risk_list",
            {
                "detectedFrom": explicit_from.isoformat(),
                "detectedTo": explicit_to.isoformat(),
            },
            trace_id="trace",
        )
    )
    call = risks.calls[0]
    assert call["detected_from"] == explicit_from
    assert call["detected_to"] == explicit_to


def test_project_scoped_query_also_applies_the_time_window() -> None:
    risks = _CapturingRisks()
    project_id = UUID(int=77)
    asyncio.run(
        _registry(risks).invoke(
            _identity(),
            "risk_list",
            {"projectId": str(project_id), "timeRange": "PREVIOUS_WEEK"},
            trace_id="trace",
        )
    )
    assert len(risks.calls) == 1
    call = risks.calls[0]
    assert call["detected_from"] == datetime(2026, 8, 10, tzinfo=SHANGHAI)
    assert call["detected_to"] == datetime(2026, 8, 17, tzinfo=SHANGHAI)


def test_registry_rejects_conflicting_time_arguments_with_validation_error() -> None:
    risks = _CapturingRisks()
    with pytest.raises(ApiError) as error:
        asyncio.run(
            _registry(risks).invoke(
                _identity(),
                "risk_list",
                {
                    "timeRange": "CURRENT_WEEK",
                    "detectedFrom": "2026-08-17T00:00:00+08:00",
                    "detectedTo": "2026-08-24T00:00:00+08:00",
                },
                trace_id="trace",
            )
        )
    assert error.value.code == "VALIDATION_ERROR"
    assert error.value.status_code == 422


def test_tool_result_adapts_to_the_agent_page_contract() -> None:
    risks = _CapturingRisks()
    result = asyncio.run(
        _registry(risks).invoke(
            _identity(), "risk_list", {"timeRange": "CURRENT_WEEK"}, trace_id="trace"
        )
    )
    assert isinstance(result.data, dict)
    assert result.tool == "risk_list"
    assert set(result.data) >= {"items", "page", "pageSize", "total"}


# ---------------------------------------------------------------------------
# System guidance and scope
# ---------------------------------------------------------------------------


class _Runtime:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def candidate_snapshot(self) -> tuple[object, ...]:
        return ()

    async def chat_snapshot(self, _snapshot: object, request: object) -> object:
        self.requests.append(request)
        return ProviderChatResponse(
            content="已查询",
            tool_calls=(),
            finish_reason=ProviderFinishReason.STOP,
            usage=ProviderTokenUsage(1, 1, 2),
            latency_ms=1,
        )


class _Tools:
    def catalogue(
        self,
        _identity: SessionIdentity,
        *,
        selected_project_id: object | None = None,
    ) -> list[dict[str, object]]:
        del selected_project_id
        return [{"name": "risk_list", "description": "risks", "argumentsSchema": {}}]

    async def invoke(
        self, _identity: SessionIdentity, name: str, arguments: object, *, trace_id: str
    ) -> object:
        del name, arguments, trace_id
        raise AssertionError("no tool call is expected for the guidance test")


def test_system_instruction_carries_time_range_guidance_and_keeps_weekly_report_intent() -> None:
    runtime = _Runtime()
    asyncio.run(
        ReadOnlyAgentCore(
            cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, _Tools())
        ).run(_identity(), "本周有哪些新增风险？")
    )
    guidance = cast(ProviderChatRequest, runtime.requests[0]).messages[0].content
    assert guidance is not None
    # Every preset is named with its natural-language trigger.
    for token in (
        "CURRENT_WEEK",
        "PREVIOUS_WEEK",
        "LAST_7_DAYS",
        "CURRENT_MONTH",
        "PREVIOUS_MONTH",
    ):
        assert token in guidance
    # The model must not compute dates itself.
    assert "不要自行计算具体日期" in guidance
    # Explicit query must go through risk_list, not the weekly report or the
    # dashboard aggregates...
    assert "risk_list" in guidance
    assert "不得用 dashboard_summary" in guidance
    # ...while 本周处理建议 stays on weekly_report (the two intents differ).
    assert "必须先调用 weekly_report" in guidance
    assert "本周处理建议" in guidance
    # The filter is the risk-added timestamp, not the last update.
    assert "detectedAt" in guidance


@pytest.mark.parametrize(
    "message",
    (
        "本周有哪些新增风险？",
        "上周有哪些新增风险？",
        "这周新增了哪些高风险？",
        "最近7天有哪些新增风险？",
        "本月新增风险有哪些？",
        "上个月新增了哪些风险？",
    ),
)
def test_relative_time_risk_questions_enter_the_agent(message: str) -> None:
    evaluation = ScopePolicy().evaluate(message)
    assert evaluation.decision is ScopeDecision.ALLOW
