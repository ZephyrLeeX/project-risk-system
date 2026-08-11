"""HTTP contracts for import history, confirmation and matching."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from risk_platform.imports.models import ImportBatchStatus
from risk_platform.shared.http import StrictRequestModel


class ImportBatchListQuery(StrictRequestModel):
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=50)


class ConfirmImportRequest(StrictRequestModel):
    acknowledgeWarnings: bool


class MatchSupplementalRequest(StrictRequestModel):
    projectId: UUID


class ProjectOption(StrictRequestModel):
    id: UUID
    externalCode: str | None
    name: str
    departmentName: str | None


class ImportRowItem(StrictRequestModel):
    id: UUID
    rowNumber: int
    action: str
    status: str
    externalCode: str | None
    projectName: str | None
    departmentName: str | None
    deliveryOwnerName: str | None
    annualPlanAmount: str | None
    actualCollectedAmount: str | None
    remainingAmount: str | None
    collectionRiskLevel: str
    collectionProgress: str | None
    warnings: list[str]
    errors: list[str]
    matchedProjectId: UUID | None
    committedProjectId: UUID | None


class SupplementalRowItem(StrictRequestModel):
    id: UUID
    rowNumber: int
    status: str
    matchStatus: str
    projectId: UUID | None
    matchedProject: ProjectOption | None
    externalCode: str | None
    projectName: str | None
    contractReceivableAmount: str | None
    procurementContractAmount: str | None
    cumulativeCollectedAmount: str | None
    remainingUncollectedAmount: str | None
    actualCollectedThisYear: str | None
    actualCollectedNetThisYear: str | None
    annualCollectionPlan: str | None
    collectionRiskLevel: str
    afterYearAmount: str | None
    warnings: list[str]
    errors: list[str]


class LegalRowItem(StrictRequestModel):
    id: UUID
    rowNumber: int
    status: str
    matchStatus: str
    projectId: UUID | None
    externalCode: str | None
    projectName: str | None
    departmentName: str | None
    deliveryOwnerName: str | None
    annualPlanAmount: str | None
    collectionRiskLevel: str
    legalProgress: str | None
    warnings: list[str]
    errors: list[str]


class ImportBatchSummary(StrictRequestModel):
    id: UUID
    fileName: str
    fileHash: str
    status: ImportBatchStatus
    sheetName: str
    totalRows: int
    readyRows: int
    warningRows: int
    errorRows: int
    createdRows: int
    updatedRows: int
    supplementalTotalRows: int
    supplementalMatchedRows: int
    supplementalUnmatchedRows: int
    supplementalAmbiguousRows: int
    supplementalWarningRows: int
    supplementalErrorRows: int
    legalTotalRows: int
    legalMatchedRows: int
    legalUnmatchedRows: int
    legalAmbiguousRows: int
    legalWarningRows: int
    legalErrorRows: int
    uploadedByName: str
    createdAt: datetime
    confirmedAt: datetime | None
    rolledBackAt: datetime | None


class ImportBatchDetail(ImportBatchSummary):
    sourceMeta: dict[str, object]
    rows: list[ImportRowItem]
    supplementalRows: list[SupplementalRowItem]
    legalRows: list[LegalRowItem]


class PaginatedImportBatches(StrictRequestModel):
    items: list[ImportBatchSummary]
    page: int
    pageSize: int
    total: int


__all__ = [
    "ConfirmImportRequest",
    "ImportBatchDetail",
    "ImportBatchListQuery",
    "ImportBatchSummary",
    "ImportRowItem",
    "LegalRowItem",
    "MatchSupplementalRequest",
    "PaginatedImportBatches",
    "ProjectOption",
    "SupplementalRowItem",
]
