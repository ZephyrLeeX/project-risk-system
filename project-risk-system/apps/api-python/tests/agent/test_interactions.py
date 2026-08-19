from uuid import uuid4

import pytest
from pydantic import ValidationError

from risk_platform.agent.interaction import _validate_action
from risk_platform.agent.models import (
    AgentEventType,
    AgentExecutionStatus,
    AgentInteractionAction,
    AgentInteractionType,
)
from risk_platform.agent.schemas import AgentInteractionRespondRequest
from risk_platform.shared.errors import ApiError


def test_interaction_contract_keeps_project_selection_and_adds_write_confirmation() -> None:
    assert AgentInteractionType.PROJECT_SELECTION.value == "PROJECT_SELECTION"
    assert {"SELECT", "MANUAL_INPUT", "CANCEL"}.issubset(
        {item.value for item in AgentInteractionAction}
    )
    assert "CONFIRM" in {item.value for item in AgentInteractionAction}
    assert {item.value for item in AgentInteractionType} == {
        "PROJECT_SELECTION",
        "WRITE_CONFIRMATION",
    }
    assert AgentExecutionStatus.WAITING_FOR_USER.value not in {"RETRY_WAIT"}
    assert AgentEventType.INTERACTION_REQUIRED.value == "interaction.required"
    assert AgentEventType.INTERACTION_RESOLVED.value == "interaction.resolved"


def test_select_and_manual_input_are_strictly_mutually_exclusive() -> None:
    project_id = uuid4()
    assert (
        AgentInteractionRespondRequest(action="SELECT", projectId=project_id).projectId
        == project_id
    )
    assert (
        AgentInteractionRespondRequest(action="MANUAL_INPUT", projectName="锡山项目").projectName
        == "锡山项目"
    )
    assert AgentInteractionRespondRequest(action="CANCEL").action == "CANCEL"

    with pytest.raises(ValidationError):
        AgentInteractionRespondRequest(action="SELECT")
    with pytest.raises(ValidationError):
        AgentInteractionRespondRequest(
            action="MANUAL_INPUT", projectName="锡山", projectId=project_id
        )
    with pytest.raises(ValidationError):
        AgentInteractionRespondRequest(action="CANCEL", projectId=project_id)


@pytest.mark.parametrize(
    ("interaction_type", "action"),
    (
        (AgentInteractionType.PROJECT_SELECTION, "CONFIRM"),
        (AgentInteractionType.WRITE_CONFIRMATION, "SELECT"),
        (AgentInteractionType.WRITE_CONFIRMATION, "MANUAL_INPUT"),
    ),
)
def test_interaction_action_allowlist_fails_closed(
    interaction_type: AgentInteractionType, action: str
) -> None:
    with pytest.raises(ApiError) as error:
        _validate_action(interaction_type, action)
    assert error.value.status_code == 409
    assert error.value.code == "AGENT_INTERACTION_ACTION_INVALID"
