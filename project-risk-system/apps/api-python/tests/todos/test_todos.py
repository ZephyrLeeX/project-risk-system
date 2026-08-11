from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from risk_platform.projects.models import ProjectRiskLevel
from risk_platform.todos.models import ActionItemSourceType, ActionItemStatus, ActionItemUrgency
from risk_platform.todos.policy import (
    build_schedule_suggestions,
    default_assignee_for_risk,
    urgency_for_risk,
)
from risk_platform.todos.schemas import ManagerTodoItem, UpdateTodoRequest


def todo_item(
    *, due_date: date | None = None, status: ActionItemStatus = ActionItemStatus.PENDING
) -> ManagerTodoItem:
    now = "2026-08-10T00:00:00Z"
    return ManagerTodoItem(
        id=uuid4(),
        riskId=None,
        projectId=uuid4(),
        projectName="项目 A",
        projectOwnerName=None,
        departmentName=None,
        title="处理风险",
        description="说明",
        urgency=ActionItemUrgency.NORMAL,
        status=status,
        sourceType=ActionItemSourceType.MANUAL,
        typeLabel="一般处理事项",
        assigneeUserId=None,
        assigneeName="管理者",
        dueDate=due_date,
        completionNote=None,
        completedAt=None,
        createdAt=now,
        updatedAt=now,
    )


def test_risk_levels_map_to_approved_todo_urgency_and_assignee() -> None:
    assert urgency_for_risk(ProjectRiskLevel.HIGH) is ActionItemUrgency.EMERGENCY
    assert urgency_for_risk(ProjectRiskLevel.MEDIUM) is ActionItemUrgency.HIGH
    assert urgency_for_risk(ProjectRiskLevel.LOW) is ActionItemUrgency.NORMAL
    assert default_assignee_for_risk(ProjectRiskLevel.HIGH, "项目经理") == "管理者"
    assert default_assignee_for_risk(ProjectRiskLevel.LOW, " 项目经理 ") == "项目经理"


def test_schedule_uses_weekday_due_dates_and_skips_completed_items() -> None:
    monday = datetime(2026, 8, 10, tzinfo=UTC)
    items = [
        todo_item(due_date=date(2026, 8, 12)),
        todo_item(status=ActionItemStatus.COMPLETED),
        todo_item(),
    ]
    schedule = build_schedule_suggestions(items, monday)
    assert [item.weekday for item in schedule] == ["周三", "周二"]
    assert schedule[0].date == date(2026, 8, 12)


def test_update_contract_rejects_unknown_fields_and_accepts_explicit_nulls() -> None:
    assert UpdateTodoRequest(dueDate=None).dueDate is None
    with pytest.raises(ValidationError):
        UpdateTodoRequest.model_validate({"model": "unexpected"})
