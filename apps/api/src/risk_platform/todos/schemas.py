"""HTTP contracts for the manager todo APIs."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.http import StrictRequestModel
from risk_platform.todos.models import ActionItemSourceType, ActionItemStatus, ActionItemUrgency


class TodoRiskResponse(BaseModel):
    id: UUID
    title: str
    description: str
    evidence: str | None
    suggestion: str | None
    level: str
    status: str
    categoryName: str
    sourceLabel: str
    detectedAt: str


class ManagerTodoItem(BaseModel):
    id: UUID
    riskId: UUID | None
    projectId: UUID
    projectName: str
    projectOwnerName: str | None
    departmentName: str | None
    title: str
    description: str
    urgency: ActionItemUrgency
    status: ActionItemStatus
    sourceType: ActionItemSourceType
    typeLabel: str
    assigneeUserId: UUID | None
    assigneeName: str
    dueDate: date | None
    completionNote: str | None
    completedAt: str | None
    createdAt: str
    updatedAt: str


class ManagerTodoSummary(BaseModel):
    total: int
    pending: int
    inProgress: int
    completed: int
    emergency: int


class ManagerTodoScheduleItem(BaseModel):
    weekday: str
    date: date
    actionItemId: UUID
    title: str
    projectName: str
    assigneeName: str
    urgency: ActionItemUrgency


class ManagerTodoListResponse(BaseModel):
    items: list[ManagerTodoItem]
    page: int
    pageSize: int
    total: int
    summary: ManagerTodoSummary
    owners: list[str]
    schedule: list[ManagerTodoScheduleItem]
    updatedAt: str | None
    dataScope: DataScopeType


class ManagerTodoDetail(ManagerTodoItem):
    risk: TodoRiskResponse | None


class ListTodosQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str | None = Field(default=None, max_length=128)
    status: ActionItemStatus | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class UpdateTodoRequest(StrictRequestModel):
    status: ActionItemStatus | None = None
    assigneeName: str | None = Field(default=None, max_length=128)
    dueDate: date | None = None
    completionNote: str | None = Field(default=None, max_length=2000)

    @field_validator("assigneeName", mode="before")
    @classmethod
    def normalize_assignee(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


__all__ = [
    "ListTodosQuery",
    "ManagerTodoDetail",
    "ManagerTodoItem",
    "ManagerTodoListResponse",
    "ManagerTodoScheduleItem",
    "ManagerTodoSummary",
    "TodoRiskResponse",
    "UpdateTodoRequest",
]
