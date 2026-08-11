"""HTTP contracts for AI provider administration."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from risk_platform.shared.http import StrictRequestModel

AiConnectionStatus = Literal["UNTESTED", "HEALTHY", "FAILED"]
AiCallResult = Literal["SUCCESS", "FAILURE"]
AiCallScene = Literal["WEEKLY_REPORT", "AGENT_QUERY", "RISK_EXTRACTION", "CONNECTION_TEST"]


class ProviderQuery(StrictRequestModel):
    keyword: str | None = Field(default=None, max_length=128)
    status: Literal["ACTIVE", "DISABLED"] | None = None


class ProviderMutation(StrictRequestModel):
    name: str = Field(min_length=2, max_length=128)
    vendor: str = Field(min_length=2, max_length=128)
    endpoint: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=128)
    expiresAt: date | None = None
    timeoutSeconds: int = Field(ge=1, le=300)
    retryCount: int = Field(ge=0, le=5)
    enabled: bool

    @field_validator("endpoint")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.strip().lower().startswith("https://"):
            raise ValueError("AI服务地址必须使用HTTPS")
        return value


class CreateProviderRequest(ProviderMutation):
    apiKey: str = Field(min_length=8, max_length=500)


class UpdateProviderRequest(ProviderMutation):
    pass


class RotateKeyRequest(StrictRequestModel):
    apiKey: str = Field(min_length=8, max_length=500)
    expiresAt: date | None = None


class ProviderStatusRequest(StrictRequestModel):
    enabled: bool


class DraftTestRequest(StrictRequestModel):
    name: str = Field(min_length=2, max_length=128)
    endpoint: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=128)
    apiKey: str = Field(min_length=8, max_length=500)
    timeoutSeconds: int = Field(ge=1, le=300)
    retryCount: int = Field(ge=0, le=5)

    @field_validator("endpoint")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.strip().lower().startswith("https://"):
            raise ValueError("AI服务地址必须使用HTTPS")
        return value


class UsageQuery(StrictRequestModel):
    scene: AiCallScene | None = None


class CallsQuery(StrictRequestModel):
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=100)
    result: AiCallResult | None = None
    scene: AiCallScene | None = None


class ProviderSummary(StrictRequestModel):
    total: int
    healthy: int
    expiring: int
    sevenDayCallTotal: int
    sevenDaySuccessRate: float


class ProviderResponse(StrictRequestModel):
    id: str
    name: str
    vendor: str
    endpoint: str
    model: str
    maskedKey: str
    expiresAt: str | None
    timeoutSeconds: int
    retryCount: int
    enabled: bool
    isDefault: bool
    priority: int
    lastTestStatus: AiConnectionStatus
    lastTestAt: str | None
    lastTestLatencyMs: int | None
    lastTestErrorCode: str | None
    sevenDayUsageCount: int
    createdAt: str
    updatedAt: str


class ProviderStrategy(StrictRequestModel):
    id: str
    name: str
    enabled: bool
    isDefault: bool
    priority: int


class ConnectionResult(StrictRequestModel):
    providerId: str | None
    providerName: str
    model: str
    success: bool
    latencyMs: int
    errorCode: str | None
    errorSummary: str | None
    testedAt: str
    traceId: str


class UsageTrend(StrictRequestModel):
    date: str
    count: int


class UsageOverview(StrictRequestModel):
    rangeStart: str
    rangeEnd: str
    callTotal: int
    successTotal: int
    successRate: float
    averageDurationMs: int
    p95DurationMs: int
    totalTokens: int
    trend: list[UsageTrend]


class CallResponse(StrictRequestModel):
    id: str
    traceId: str
    providerName: str
    model: str
    scene: AiCallScene
    totalTokens: int
    durationMs: int
    result: AiCallResult
    errorCode: str | None
    errorSummary: str | None
    createdAt: str


class CallDetail(CallResponse):
    inputTokens: int
    outputTokens: int
    actorDisplayName: str | None
    dataProtectionNotice: str


class PageResponse(StrictRequestModel):
    items: list[CallResponse]
    page: int
    pageSize: int
    total: int


__all__ = [
    "CallDetail",
    "CallResponse",
    "CallsQuery",
    "ConnectionResult",
    "CreateProviderRequest",
    "DraftTestRequest",
    "PageResponse",
    "ProviderQuery",
    "ProviderResponse",
    "ProviderStatusRequest",
    "ProviderStrategy",
    "ProviderSummary",
    "RotateKeyRequest",
    "UpdateProviderRequest",
    "UsageOverview",
    "UsageQuery",
]
