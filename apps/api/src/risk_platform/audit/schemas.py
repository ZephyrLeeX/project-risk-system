"""HTTP contracts for the metadata-only audit query surface."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, field_validator

from risk_platform.shared.http import StrictRequestModel


class AuditModuleKey(StrEnum):
    ALL = "ALL"
    AUTH = "AUTH"
    PERMISSION = "PERMISSION"
    MAILBOX = "MAILBOX"
    AI = "AI"
    RISK = "RISK"
    IMPORT = "IMPORT"
    CONFIG = "CONFIG"
    AUDIT = "AUDIT"
    OTHER = "OTHER"


class AuditActionGroup(StrEnum):
    ALL = "ALL"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    TEST = "TEST"
    LOGIN = "LOGIN"
    PUBLISH = "PUBLISH"
    ROLLBACK = "ROLLBACK"
    EXPORT = "EXPORT"
    OTHER = "OTHER"


class AuditDateRange(StrEnum):
    TODAY = "TODAY"
    SEVEN_DAYS = "7_DAYS"
    THIRTY_DAYS = "30_DAYS"
    CUSTOM = "CUSTOM"


class AuditExportFormat(StrEnum):
    XLSX = "XLSX"
    CSV = "CSV"


class AuditFilter(StrictRequestModel):
    keyword: str | None = Field(default=None, max_length=128)
    module: AuditModuleKey = AuditModuleKey.ALL
    action: AuditActionGroup = AuditActionGroup.ALL
    result: str | None = Field(default=None, pattern="^(SUCCESS|FAILURE)$")
    dateRange: AuditDateRange = AuditDateRange.TODAY
    startDate: date | None = None
    endDate: date | None = None
    sensitiveOnly: bool = False

    @field_validator("endDate")
    @classmethod
    def end_date_is_not_before_start(cls, value: date | None, info: object) -> date | None:
        start = getattr(info, "data", {}).get("startDate")
        if value is not None and start is not None and value < start:
            raise ValueError("开始日期不能晚于结束日期")
        return value


class AuditListQuery(AuditFilter):
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=100)


class AuditExportRequest(AuditFilter):
    format: AuditExportFormat
    reason: str = Field(min_length=4, max_length=200)


class AuditLogOption(StrictRequestModel):
    value: str
    label: str
    count: int


class AuditLogOptions(StrictRequestModel):
    modules: list[AuditLogOption]
    actions: list[AuditLogOption]


class AuditLogSummary(StrictRequestModel):
    todayCount: int
    yesterdayCount: int
    dayChange: int
    failedCount: int
    sensitiveCount: int = 0
    activeActorCount: int
    systemAdminActorCount: int


class AuditLogListItem(StrictRequestModel):
    id: UUID
    eventId: str
    createdAt: datetime
    actorName: str
    actorAccount: str | None
    actorRole: str | None
    module: AuditModuleKey
    moduleLabel: str
    rawModule: str
    action: str
    actionLabel: str
    actionGroup: AuditActionGroup
    resourceType: str
    resourceId: str | None
    resourceLabel: str
    summary: str
    result: str
    traceId: str
    clientIp: str = ""
    client: str = ""
    errorCode: str | None
    isSensitive: bool = False


class AuditLogDetail(AuditLogListItem):
    beforeSnapshot: None = None
    afterSnapshot: None = None
    beforeSummary: str = ""
    afterSummary: str = ""
    context: str
    previousHash: str | None
    integrityHash: str | None


class AuditLogIntegrity(StrictRequestModel):
    status: str
    totalRecords: int
    verifiedRecords: int
    firstBrokenEventId: UUID | None
    lastVerifiedAt: datetime
    appendOnly: bool = True


class PaginatedAuditLogs(StrictRequestModel):
    items: list[AuditLogListItem]
    page: int
    pageSize: int
    total: int


__all__ = [
    "AuditActionGroup",
    "AuditDateRange",
    "AuditExportFormat",
    "AuditExportRequest",
    "AuditFilter",
    "AuditListQuery",
    "AuditLogDetail",
    "AuditLogIntegrity",
    "AuditLogOptions",
    "AuditLogSummary",
    "AuditModuleKey",
    "PaginatedAuditLogs",
]
