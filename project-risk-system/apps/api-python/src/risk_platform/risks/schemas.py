"""Compatible risk lifecycle and timeline DTOs."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from risk_platform.rbac.models import DataScopeType
from risk_platform.risks.models import ProjectRiskLevel, RiskSourceType, RiskStatus
from risk_platform.shared.http import StrictRequestModel
from risk_platform.timeline.models import RiskTimelineEventType


class RiskCategoryOption(BaseModel):
    id: UUID
    code: str
    name: str


class RiskItem(BaseModel):
    id: UUID
    projectId: UUID
    projectName: str
    projectExternalCode: str | None
    departmentName: str | None
    projectOwnerName: str | None
    title: str
    description: str
    evidence: str | None
    suggestion: str | None
    level: ProjectRiskLevel
    status: RiskStatus
    category: RiskCategoryOption
    sourceType: RiskSourceType
    sourceLabel: str
    reporterName: str | None
    weekCode: str | None
    detectedAt: str
    updatedAt: str


class RiskDetail(RiskItem):
    resolvedAt: str | None
    resolvedByName: str | None
    resolutionReason: str | None


class RiskPage(BaseModel):
    items: list[RiskItem]
    page: int
    pageSize: int
    total: int


class ResolvedRiskPage(RiskPage):
    items: list[RiskItem]
    owners: list[str]
    updatedAt: str | None
    dataScope: DataScopeType


class RiskQuery(StrictRequestModel):
    keyword: str | None = Field(default=None, max_length=100)
    level: ProjectRiskLevel | None = None
    categoryId: UUID | None = None
    owner: str | None = Field(default=None, max_length=100)
    sourceType: RiskSourceType | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class TimelineQuery(StrictRequestModel):
    keyword: str | None = Field(default=None, max_length=100)
    level: ProjectRiskLevel | None = None
    eventType: RiskTimelineEventType | None = None
    projectId: UUID | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class LifecycleRequest(StrictRequestModel):
    reason: str = Field(min_length=5, max_length=2000)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("reason is too short")
        return value


class TimelineItem(BaseModel):
    id: UUID
    eventType: RiskTimelineEventType
    eventLabel: str
    tone: str
    projectId: UUID
    projectName: str
    departmentName: str | None
    projectOwnerName: str | None
    riskId: UUID
    riskTitle: str
    riskLevel: ProjectRiskLevel
    riskStatus: RiskStatus
    categoryName: str
    title: str
    description: str
    fromValue: str | None
    toValue: str | None
    actorName: str
    sourceLabel: str
    occurredAt: str


class TimelinePage(BaseModel):
    items: list[TimelineItem]
    page: int
    pageSize: int
    total: int
    summary: dict[str, int]
    projects: list[dict[str, UUID | str]]
    updatedAt: str | None
    dataScope: DataScopeType


class TimelineDetail(TimelineItem):
    riskDescription: str
    riskEvidence: str | None
    riskSuggestion: str | None
    detectedAt: str
    resolvedAt: str | None
    resolutionReason: str | None
    metadata: dict[str, object] | None


__all__ = [
    "LifecycleRequest",
    "ResolvedRiskPage",
    "RiskDetail",
    "RiskItem",
    "RiskPage",
    "RiskQuery",
    "TimelineDetail",
    "TimelineItem",
    "TimelinePage",
    "TimelineQuery",
]
