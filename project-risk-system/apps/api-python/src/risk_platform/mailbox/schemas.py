from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from risk_platform.shared.http import StrictRequestModel

MailboxProvider = Literal["QQ", "IMAP"]
MailboxEncryption = Literal["SSL", "STARTTLS"]
InitialSyncWeeks = Literal[1, 4, 8, 12]


class MailboxConfigRequest(StrictRequestModel):
    provider: MailboxProvider
    email: str = Field(min_length=3, max_length=255)
    authCode: str | None = Field(default=None, min_length=6, max_length=255)
    imapHost: str = Field(min_length=1, max_length=255)
    imapPort: int = Field(ge=1, le=65_535)
    encryption: MailboxEncryption
    folder: str = Field(min_length=1, max_length=255)
    subjectKeywords: list[str] = Field(min_length=1, max_length=8)
    senderRule: str | None = Field(default=None, max_length=255)
    initialSyncWeeks: InitialSyncWeeks
    readAttachments: bool
    aiExtractionEnabled: bool

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or "." not in normalized.rsplit("@", 1)[1]:
            raise ValueError("请输入有效的邮箱地址")
        return normalized

    @field_validator("folder")
    @classmethod
    def valid_folder(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            raise ValueError("邮件文件夹名称格式不正确")
        return normalized

    @field_validator("imapHost")
    @classmethod
    def valid_host(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or any(char.isspace() for char in normalized):
            raise ValueError("IMAP服务器地址格式不正确")
        if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for char in normalized):
            raise ValueError("IMAP服务器地址格式不正确")
        return normalized

    @field_validator("subjectKeywords")
    @classmethod
    def clean_keywords(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not cleaned or any(len(item) > 20 for item in cleaned):
            raise ValueError("主题关键词格式不正确")
        return cleaned


class MailboxStatusRequest(StrictRequestModel):
    enabled: bool


class MailboxOverview(StrictRequestModel):
    configured: bool
    provider: MailboxProvider
    email: str
    maskedEmail: str | None
    hasAuthCode: bool
    authCodeLast4: str | None
    imapHost: str
    imapPort: int
    encryption: MailboxEncryption
    folder: str
    subjectKeywords: list[str]
    senderRule: str
    initialSyncWeeks: InitialSyncWeeks
    readAttachments: bool
    aiExtractionEnabled: bool
    enabled: bool
    autoSyncEnabled: bool
    autoSyncIntervalMinutes: int
    connectionStatus: Literal["UNTESTED", "HEALTHY", "FAILED"]
    lastTestAt: str | None
    lastTestLatencyMs: int | None
    lastTestErrorCode: str | None
    lastTestErrorSummary: str | None
    lastSyncAt: str | None
    lastSyncStatus: Literal["QUEUED", "RUNNING", "SUCCESS", "PARTIAL", "FAILURE"] | None
    lastSyncNewCount: int
    lastSyncSuccessCount: int
    lastSyncRiskCandidateCount: int
    lastSyncFailedCount: int
    nextSyncAt: str | None
    uidCursor: str | None
    totalSyncedCount: int
    totalRiskCandidateCount: int
    updatedAt: str | None


class MailboxConnectionTestResult(StrictRequestModel):
    success: bool
    status: Literal["UNTESTED", "HEALTHY", "FAILED"]
    latencyMs: int
    testedAt: str
    folder: str
    errorCode: str | None
    errorSummary: str | None


class MailSyncBatchResponse(StrictRequestModel):
    id: str
    code: str
    trigger: Literal["MANUAL", "SCHEDULED", "RETRY"]
    status: Literal["QUEUED", "RUNNING", "SUCCESS", "PARTIAL", "FAILURE"]
    createdAt: str
    startedAt: str | None
    finishedAt: str | None
    discoveredCount: int
    handedOffCount: int
    duplicateCount: int
    downstreamPendingCount: int
    scannedCount: int
    newCount: int
    successCount: int
    skippedCount: int
    failedCount: int
    riskCandidateCount: int
    errorSummary: str | None


class MailRiskCandidateUpdateRequest(StrictRequestModel):
    projectId: UUID
    categoryId: UUID
    level: Literal["HIGH", "MEDIUM", "LOW"]
    description: str = Field(min_length=4, max_length=4000)
    evidence: str = Field(min_length=2, max_length=4000)
    suggestion: str = Field(min_length=2, max_length=4000)


class MailRiskCandidateResponse(StrictRequestModel):
    id: UUID
    projectId: UUID
    projectName: str
    categoryId: UUID
    categoryName: str
    level: Literal["HIGH", "MEDIUM", "LOW"]
    levelLabel: str
    description: str
    evidence: str
    suggestion: str
    confidence: int
    status: Literal["PENDING", "CONFIRMED", "IGNORED"]
    confirmedRiskId: UUID | None
    reviewedAt: str | None
