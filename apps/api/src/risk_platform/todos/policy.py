"""Pure rules shared by todo queries and risk-to-todo callers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from uuid import UUID

from risk_platform.projects.models import ProjectRiskLevel
from risk_platform.todos.models import ActionItemUrgency
from risk_platform.todos.schemas import ManagerTodoItem, ManagerTodoScheduleItem


class RiskForTodo(Protocol):
    id: UUID
    projectId: UUID
    level: ProjectRiskLevel
    title: str
    description: str
    suggestion: str | None


def urgency_for_risk(level: ProjectRiskLevel | str) -> ActionItemUrgency:
    value = ProjectRiskLevel(level)
    return {
        ProjectRiskLevel.HIGH: ActionItemUrgency.EMERGENCY,
        ProjectRiskLevel.MEDIUM: ActionItemUrgency.HIGH,
    }.get(value, ActionItemUrgency.NORMAL)


def default_assignee_for_risk(level: ProjectRiskLevel | str, owner_name: str | None) -> str:
    if ProjectRiskLevel(level) is ProjectRiskLevel.HIGH:
        return "管理者"
    return (owner_name or "管理者").strip() or "管理者"


def start_of_week(value: date | datetime) -> date:
    current = value.date() if isinstance(value, datetime) else value
    return current - timedelta(days=current.weekday())


def build_schedule_suggestions(
    items: list[ManagerTodoItem], now: datetime | None = None
) -> list[ManagerTodoScheduleItem]:
    monday = start_of_week(now or datetime.now(UTC))
    active = [item for item in items if item.status.value != "COMPLETED"][:5]
    weekdays = ("周一", "周二", "周三", "周四", "周五")
    result: list[ManagerTodoScheduleItem] = []
    for index, item in enumerate(active):
        suggested = monday + timedelta(days=index)
        due_date = item.dueDate
        if due_date is not None and 0 <= (due_date - monday).days < 5:
            target = due_date
        else:
            target = suggested
        result.append(
            ManagerTodoScheduleItem(
                weekday=weekdays[(target - monday).days],
                date=target,
                actionItemId=item.id,
                title=item.title,
                projectName=item.projectName,
                assigneeName=item.assigneeName,
                urgency=item.urgency,
            )
        )
    return result


__all__ = [
    "RiskForTodo",
    "build_schedule_suggestions",
    "default_assignee_for_risk",
    "start_of_week",
    "urgency_for_risk",
]
