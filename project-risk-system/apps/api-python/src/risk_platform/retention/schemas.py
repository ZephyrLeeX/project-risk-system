"""Approved HTTP contracts for auditable retention-hold management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from risk_platform.retention.models import (
    RetentionHold,
    RetentionHoldReason,
    RetentionHoldStatus,
    RetentionResourceType,
)
from risk_platform.shared.http import StrictRequestModel


class CreateRetentionHoldRequest(StrictRequestModel):
    resourceType: RetentionResourceType
    resourceId: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$",
    )
    reason: RetentionHoldReason
    expiresAt: datetime | None

    @field_validator("expiresAt")
    @classmethod
    def expires_at_must_be_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("expiresAt 必须为 UTC 时间")
        return value

    @model_validator(mode="after")
    def validate_resource_id(self) -> CreateRetentionHoldRequest:
        if self.resourceType in {
            RetentionResourceType.IMPORT_BATCH,
            RetentionResourceType.AGENT_CONVERSATION,
        }:
            try:
                self.resourceId = str(UUID(self.resourceId))
            except ValueError:
                raise ValueError("resourceId 必须为 UUID") from None
        return self


class ReleaseRetentionHoldRequest(StrictRequestModel):
    pass


class RetentionHoldQuery(StrictRequestModel):
    resourceType: RetentionResourceType | None = None
    resourceId: str | None = Field(default=None, min_length=1, max_length=128)
    status: RetentionHoldStatus | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=30, ge=1, le=100)


class RetentionHoldResponse(StrictRequestModel):
    id: str
    resourceType: RetentionResourceType
    resourceId: str
    reason: RetentionHoldReason
    status: RetentionHoldStatus
    createdAt: str
    createdById: str
    expiresAt: str | None
    releasedAt: str | None
    releasedById: str | None
    expiredAt: str | None
    expiredById: str | None

    @classmethod
    def from_hold(cls, hold: RetentionHold) -> RetentionHoldResponse:
        return cls(
            id=str(hold.id),
            resourceType=hold.resourceType,
            resourceId=hold.resourceId,
            reason=hold.reason,
            status=hold.status,
            createdAt=_format_utc(hold.createdAt),
            createdById=str(hold.createdById),
            expiresAt=_format_optional_utc(hold.expiresAt),
            releasedAt=_format_optional_utc(hold.releasedAt),
            releasedById=str(hold.releasedById) if hold.releasedById is not None else None,
            expiredAt=_format_optional_utc(hold.expiredAt),
            expiredById=str(hold.expiredById) if hold.expiredById is not None else None,
        )


class RetentionHoldListResponse(StrictRequestModel):
    items: list[RetentionHoldResponse]
    total: int
    page: int
    pageSize: int


def _format_optional_utc(value: datetime | None) -> str | None:
    return _format_utc(value) if value is not None else None


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "CreateRetentionHoldRequest",
    "ReleaseRetentionHoldRequest",
    "RetentionHoldListResponse",
    "RetentionHoldQuery",
    "RetentionHoldResponse",
]
