"""Dashboard response contracts retained from the existing frontend API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from risk_platform.rbac.models import DataScopeType
from risk_platform.risks.models import ProjectRiskLevel
from risk_platform.risks.schemas import RiskItem


class DashboardSummary(BaseModel):
    projectTotal: int
    deliveryProjectTotal: int
    deliveryDepartmentTotal: int
    latestImportBatchCode: str | None
    latestImportCreatedProjectTotal: int
    activeRiskTotal: int
    highRiskTotal: int
    mediumRiskTotal: int
    lowRiskTotal: int
    unknownRiskTotal: int
    riskProjectTotal: int
    highRiskProjectTotal: int
    weeklyNewRiskTotal: int
    weeklyNewHighRiskTotal: int
    mailAiRiskTotal: int
    manualRiskTotal: int
    excelRiskTotal: int
    litigationRiskTotal: int
    highRiskFocusProjectNames: list[str]
    highRiskPriorityItems: list[str]
    riskRemainingAmountYuan: str | None
    riskCollectedAmountYuan: str | None
    riskAmountCompleteProjectTotal: int
    riskAmountMissingProjectTotal: int
    riskCollectionCompletionRate: float | None
    updatedAt: str | None
    dataScope: DataScopeType


class CollectionTotals(BaseModel):
    projectTotal: int
    amountCompleteProjectTotal: int
    amountMissingProjectTotal: int
    receivableAmountYuan: str | None
    collectedAmountYuan: str | None
    remainingAmountYuan: str | None
    completionRate: float | None


class DepartmentCollectionItem(CollectionTotals):
    departmentId: UUID | None
    departmentKey: str
    departmentName: str


class DepartmentCollectionSummary(BaseModel):
    items: list[DepartmentCollectionItem]
    totals: CollectionTotals
    pendingSupplementalCount: int | None
    pendingSupplementalReceivableAmountYuan: str | None
    updatedAt: str | None
    dataScope: DataScopeType


class CollectionProjectItem(BaseModel):
    projectId: UUID
    externalCode: str | None
    projectName: str
    ownerName: str | None
    amountSource: str
    amountSourceLabel: str
    supplementalRowCount: int
    receivableAmountYuan: str | None
    collectedAmountYuan: str | None
    remainingAmountYuan: str | None
    completionRate: float | None


class DepartmentCollectionDetail(BaseModel):
    departmentId: UUID | None
    departmentKey: str
    departmentName: str
    summary: CollectionTotals
    projects: list[CollectionProjectItem]
    updatedAt: str | None


class NextCollectionInfo(BaseModel):
    source: str
    month: int | None
    attribute: str | None
    amountYuan: str | None
    label: str


class RiskCollectionProjectItem(CollectionProjectItem):
    departmentName: str | None
    riskLevel: str
    activeRiskTotal: int
    collectionProgress: str | None
    nextCollection: NextCollectionInfo
    updatedAt: str | None


class RiskCollectionListResponse(BaseModel):
    items: list[RiskCollectionProjectItem]
    totals: CollectionTotals
    riskProjectTotal: int
    owners: list[str]
    updatedAt: str | None
    dataScope: DataScopeType


class RiskCollectionMonthItem(BaseModel):
    month: int
    attribute: str | None
    amountYuan: str | None


class ActiveCollectionRisk(BaseModel):
    id: UUID
    title: str
    description: str
    level: str
    categoryName: str
    sourceLabel: str
    detectedAt: str


class RiskCollectionDetail(RiskCollectionProjectItem):
    monthlyCollections: list[RiskCollectionMonthItem]
    activeRisks: list[ActiveCollectionRisk]
    statisticalScope: str


class CollectionQuery(BaseModel):
    keyword: str | None = Field(default=None, max_length=100)
    level: ProjectRiskLevel | None = None
    owner: str | None = Field(default=None, max_length=100)


DashboardFocusItem = RiskItem


__all__ = [
    "CollectionProjectItem",
    "CollectionQuery",
    "CollectionTotals",
    "DashboardFocusItem",
    "DashboardSummary",
    "DepartmentCollectionDetail",
    "DepartmentCollectionItem",
    "DepartmentCollectionSummary",
    "RiskCollectionDetail",
    "RiskCollectionListResponse",
    "RiskCollectionProjectItem",
]
