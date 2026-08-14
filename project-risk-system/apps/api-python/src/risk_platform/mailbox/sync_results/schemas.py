"""T043 mailbox sync-results browse/retry surface response contracts.

These Pydantic models mirror the frozen ``@risk-platform/contracts`` types
(``MailSyncSummary``, ``MailRiskReviewOptions``, ``MailMessageListResponse``,
``MailMessageDetail``, ``MailSyncBatchItem``, ``MailSyncBatchDetail``) under the
ADR 0001 backward-compatibility baseline. Field names are camelCase so the
serialized JSON matches the existing frontend contract exactly.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from risk_platform.shared.http import StrictRequestModel

MailSyncStatus = Literal["QUEUED", "RUNNING", "SUCCESS", "PARTIAL", "FAILURE"]
MailSyncTrigger = Literal["MANUAL", "SCHEDULED", "RETRY"]
MailMessageStatus = Literal["ANALYZING", "COMPLETED", "SKIPPED", "FAILED"]
MailRiskCandidateStatus = Literal["PENDING", "CONFIRMED", "IGNORED"]
ProjectRiskLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
MailProjectMatchType = Literal["EXACT", "ALIAS", "FUZZY", "MANUAL"]
MailAttachmentStatus = Literal["PARSED", "SKIPPED", "FAILED"]
MailProcessingTraceStatus = Literal["COMPLETED", "SKIPPED", "FAILED", "RUNNING"]


class MailSyncBatchItem(StrictRequestModel):
    id: str
    code: str
    trigger: MailSyncTrigger
    status: MailSyncStatus
    createdAt: str
    startedAt: str | None
    finishedAt: str | None
    scannedCount: int
    newCount: int
    successCount: int
    skippedCount: int
    failedCount: int
    riskCandidateCount: int
    errorSummary: str | None


class MailSyncSummary(StrictRequestModel):
    configured: bool
    maskedEmail: str | None
    latestBatch: MailSyncBatchItem | None
    latestScannedCount: int
    latestNewCount: int
    latestSuccessCount: int
    latestSkippedCount: int
    latestDuplicateCount: int
    latestRuleMismatchCount: int
    latestFailedCount: int
    latestRiskCandidateCount: int
    latestPendingRiskCount: int
    historicalFailedCount: int


class MailProjectMatchItem(StrictRequestModel):
    id: str
    projectId: str
    projectName: str
    matchType: MailProjectMatchType
    confidence: int
    matchedText: str


class MailAttachmentItem(StrictRequestModel):
    name: str
    type: str
    sizeBytes: int
    status: MailAttachmentStatus
    summary: str | None


class MailProcessingTraceItem(StrictRequestModel):
    stage: str
    status: MailProcessingTraceStatus
    detail: str
    occurredAt: str


class MailRiskCandidateItem(StrictRequestModel):
    id: str
    projectId: str
    projectName: str
    categoryId: str
    categoryName: str
    level: ProjectRiskLevel
    levelLabel: str
    description: str
    evidence: str
    suggestion: str
    confidence: int
    status: MailRiskCandidateStatus
    confirmedRiskId: str | None
    reviewedAt: str | None


class MailMessageListItem(StrictRequestModel):
    id: str
    batchId: str
    batchCode: str
    status: MailMessageStatus
    subject: str
    senderName: str | None
    senderAddress: str | None
    sentAt: str | None
    processedAt: str | None
    projectMatches: list[MailProjectMatchItem]
    riskCandidateCount: int
    pendingRiskCount: int
    resultLabel: str
    resultNote: str
    failureSummary: str | None


class MailMessageListResponse(StrictRequestModel):
    items: list[MailMessageListItem]
    page: int
    pageSize: int
    total: int
    historicalFailedCount: int


class MailMessageDetail(MailMessageListItem):
    keyPoints: list[str]
    sanitizedSummary: str | None
    attachments: list[MailAttachmentItem]
    processingTrace: list[MailProcessingTraceItem]
    riskCandidates: list[MailRiskCandidateItem]
    retryCount: int


class MailSyncBatchDetail(MailSyncBatchItem):
    operatorName: str
    durationMs: int | None
    startUid: str | None
    endUid: str | None
    messages: list[MailMessageListItem]


class MailSyncBatchListResponse(StrictRequestModel):
    items: list[MailSyncBatchItem]
    page: int
    pageSize: int
    total: int


class ReviewOptionItem(StrictRequestModel):
    id: str
    name: str


class RiskLevelOption(StrictRequestModel):
    value: ProjectRiskLevel
    label: str


class MailRiskReviewOptions(StrictRequestModel):
    projects: list[ReviewOptionItem]
    categories: list[ReviewOptionItem]
    levels: list[RiskLevelOption]


class MailMessageListQuery(StrictRequestModel):
    """Compatible query parameters for ``GET /mailbox/messages``."""

    keyword: str | None = None
    status: MailMessageStatus | None = None
    batchId: UUID | None = None
    withRisk: bool | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=100)


class MailSyncBatchListQuery(StrictRequestModel):
    """Compatible query parameters for ``GET /mailbox/sync-batches``."""

    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=100)


__all__ = [
    "MailAttachmentItem",
    "MailMessageDetail",
    "MailMessageListItem",
    "MailMessageListQuery",
    "MailMessageListResponse",
    "MailMessageStatus",
    "MailProcessingTraceItem",
    "MailProjectMatchItem",
    "MailRiskCandidateItem",
    "MailRiskCandidateStatus",
    "MailRiskReviewOptions",
    "MailSyncBatchDetail",
    "MailSyncBatchItem",
    "MailSyncBatchListQuery",
    "MailSyncBatchListResponse",
    "MailSyncStatus",
    "MailSyncSummary",
    "MailSyncTrigger",
    "ProjectRiskLevel",
    "ReviewOptionItem",
    "RiskLevelOption",
]
