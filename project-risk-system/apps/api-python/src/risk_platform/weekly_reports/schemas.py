"""Public weekly-report contracts approved by ADRs 0019 and 0021."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer

from risk_platform.model_types import JSONValue
from risk_platform.risks.models import ProjectRiskLevel, RiskStatus
from risk_platform.todos.models import ActionItemStatus


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_values(self, value: object) -> object:
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return value


class WeeklyProject(_Contract):
    id: UUID
    name: str


class WeeklyProjectSummary(_Contract):
    project: WeeklyProject
    summary: dict[str, JSONValue]
    riskCount: int
    riskLevelCounts: dict[str, JSONValue]
    sourceRevision: int


class WeeklyReportItemResponse(_Contract):
    sourceMailId: UUID
    sourceCandidateId: UUID
    riskId: UUID
    todoId: UUID
    sourceRevision: int
    summary: str
    riskLevel: ProjectRiskLevel
    riskStatus: RiskStatus
    todoStatus: ActionItemStatus
    occurredAt: datetime


class WeeklyReportResponse(_Contract):
    weekStart: date
    weekEnd: date
    generatedAt: datetime
    stale: bool
    freshnessDeadline: datetime
    summary: dict[str, JSONValue]
    projects: list[WeeklyProjectSummary]


class WeeklyProjectDetail(_Contract):
    weekStart: date
    project: WeeklyProject
    items: list[WeeklyReportItemResponse]
    generatedAt: datetime
    stale: bool


__all__ = [
    "WeeklyProject",
    "WeeklyProjectDetail",
    "WeeklyProjectSummary",
    "WeeklyReportItemResponse",
    "WeeklyReportResponse",
]
