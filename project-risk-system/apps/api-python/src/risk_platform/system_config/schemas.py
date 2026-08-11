"""HTTP contracts for versioned system configuration."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from risk_platform.shared.http import StrictRequestModel

ConfigModule = Literal["ALL", "RISK", "MAIL", "ALIAS", "SECURITY", "NOTIFICATION"]
RiskLevel = Literal["HIGH", "MEDIUM", "LOW"]


class RiskCategory(StrictRequestModel):
    id: UUID | None = None
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=128)
    keywords: list[str] = Field(max_length=30)
    colorToken: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    description: str | None = Field(default=None, max_length=500)
    defaultLevel: RiskLevel | None = None
    sortOrder: int = Field(ge=0, le=10_000)
    isActive: bool
    riskCount: int = 0


class RiskLevelRule(StrictRequestModel):
    level: RiskLevel
    displayName: str = Field(min_length=2, max_length=32)
    colorToken: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    criteria: str = Field(min_length=4, max_length=500)
    keywords: list[str] = Field(max_length=30)
    sortOrder: int = Field(ge=0, le=10_000)
    isActive: bool


class ProjectAlias(StrictRequestModel):
    id: UUID | None = None
    projectId: UUID
    projectName: str = ""
    projectCode: str | None = None
    projectOwnerName: str | None = None
    alias: str = Field(min_length=1, max_length=255)
    source: str = Field(max_length=64)
    note: str | None = Field(default=None, max_length=500)
    isActive: bool
    hitCount: int = 0
    lastHitAt: str | None = None


class MailSettings(StrictRequestModel):
    syncIntervalMinutes: Literal[15, 30, 60, 120]
    initialSyncDays: int = Field(ge=1, le=365)
    subjectKeywords: list[str] = Field(min_length=1, max_length=30)
    riskKeywords: list[str] = Field(min_length=1, max_length=50)


class SecuritySettings(StrictRequestModel):
    sessionHours: int = Field(ge=1, le=24)
    idleTimeoutMinutes: int = Field(ge=10, le=240)
    loginMaxAttempts: int = Field(ge=3, le=10)
    loginLockMinutes: int = Field(ge=5, le=120)
    passwordMinLength: int = Field(ge=8, le=128)


class NotificationSettings(StrictRequestModel):
    mailboxSyncFailure: bool
    apiKeyExpiry: bool
    apiKeyExpiryDays: int = Field(ge=1, le=180)
    importFailure: bool
    abnormalLogin: bool


class ConfigSnapshot(StrictRequestModel):
    categories: list[RiskCategory] = Field(min_length=1, max_length=50)
    levels: list[RiskLevelRule] = Field(min_length=3, max_length=3)
    aliases: list[ProjectAlias] = Field(max_length=1_000)
    mail: MailSettings
    security: SecuritySettings
    notifications: NotificationSettings


class PublishRequest(ConfigSnapshot):
    changeCount: int = Field(ge=1, le=10_000)
    changeSummary: str = Field(min_length=4, max_length=500)
    module: ConfigModule

    @model_validator(mode="after")
    def validate_complete_levels(self) -> PublishRequest:
        if {item.level for item in self.levels} != {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("高、中、低三级风险规则必须完整")
        return self


class ReleaseQuery(StrictRequestModel):
    limit: int = Field(default=30, ge=1, le=100)
    module: ConfigModule | None = None


class ProjectOptionResponse(StrictRequestModel):
    id: str
    externalCode: str | None
    name: str
    departmentName: str | None


class ConfigOverview(ConfigSnapshot):
    version: str
    publishedAt: str
    publishedBy: str
    changeSummary: str
    activeConfigCount: int
    activeCategoryCount: int
    activeLevelCount: int
    monthlyChangeCount: int
    lastMailboxSyncAt: str | None
    nextMailboxSyncAt: str | None
    authorizedMailboxCount: int


class ReleaseItem(StrictRequestModel):
    id: str
    version: str
    module: ConfigModule
    changeCount: int
    changeSummary: str
    impactScope: list[str]
    publishedAt: str
    publishedBy: str
    traceId: str


class ReleaseDetail(ReleaseItem):
    beforeSnapshot: ConfigSnapshot | None
    snapshot: ConfigSnapshot


def clean_keywords(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


__all__ = [
    "ConfigOverview",
    "ConfigSnapshot",
    "MailSettings",
    "NotificationSettings",
    "ProjectAlias",
    "ProjectOptionResponse",
    "PublishRequest",
    "ReleaseDetail",
    "ReleaseItem",
    "ReleaseQuery",
    "RiskCategory",
    "RiskLevelRule",
    "SecuritySettings",
    "clean_keywords",
]
