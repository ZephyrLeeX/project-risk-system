from uuid import uuid4

import pytest
from pydantic import ValidationError

from risk_platform.agent.models import MutationDraftOperation
from risk_platform.agent.mutations import proposal_tool_names
from risk_platform.agent.schemas import (
    AgentInteractionRespondRequest,
    CandidateRisk,
    CandidateRiskBasisType,
    MutationProposalRequest,
)
from risk_platform.model_types import JSONValue
from risk_platform.risks.models import RiskSourceType


def test_model_write_catalogue_is_exactly_six_proposals_and_never_commit() -> None:
    assert proposal_tool_names() == tuple(item.value for item in MutationDraftOperation)
    assert all(name.endswith("_proposal") for name in proposal_tool_names())
    assert "risk_create" not in proposal_tool_names()
    assert "commit" not in " ".join(proposal_tool_names())


def test_write_confirmation_supports_editable_fields_but_project_selection_stays_strict() -> None:
    fields: dict[str, JSONValue] = {"projectId": str(uuid4()), "title": "编辑后的标题"}
    request = AgentInteractionRespondRequest(action="CONFIRM", finalFields=fields)
    assert request.finalFields == fields
    with pytest.raises(ValidationError):
        AgentInteractionRespondRequest(action="CONFIRM", finalFields=fields, projectId=uuid4())


def test_risk_create_does_not_require_responsibility_or_due_date() -> None:
    request = MutationProposalRequest(
        projectId=uuid4(),
        category=uuid4(),
        title="尾款逾期未支付",
        description="项目尾款已逾期未支付",
        level="MEDIUM",
        evidence="用户陈述：具体金额及合同付款日未提供。",
        suggestion="核对合同条款并持续催收。",
    )
    assert request.assigneeUserId is None
    assert request.dueDate is None


def test_candidate_risk_basis_distinguishes_ai_analysis_from_system_fact() -> None:
    base = {
        "id": uuid4(),
        "projectId": uuid4(),
        "projectName": "项目",
        "title": "供应链风险",
        "description": "模型分析提示风险",
    }
    ai = CandidateRisk.model_validate(
        {
            **base,
            "basisType": CandidateRiskBasisType.AI_ANALYSIS,
            "evidenceSummary": "AI风险分析 - 基于模型判断",
        }
    )
    assert ai.sourceInvocationIds == []
    with pytest.raises(ValidationError):
        CandidateRisk.model_validate(
            {
                **base,
                "basisType": CandidateRiskBasisType.SYSTEM_FACT,
                "evidenceSummary": "系统事实",
            }
        )


def test_agent_source_type_is_a_distinct_business_enum() -> None:
    assert RiskSourceType.AGENT.value == "AGENT"


def test_proposal_schema_rejects_mass_assignment_fields() -> None:
    with pytest.raises(ValidationError):
        MutationProposalRequest(
            projectId=uuid4(),
            title="风险",
            description="描述",
            reporterUserId=uuid4(),  # type: ignore[call-arg]
        )
