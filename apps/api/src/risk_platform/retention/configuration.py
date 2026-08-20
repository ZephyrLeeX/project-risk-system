"""Strict, versioned retention settings and frozen-fact calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import Field

from risk_platform.shared.http import StrictRequestModel

DEFAULT_RETENTION_CONFIG_VERSION = "ADR0027_DEFAULT"


class RetentionSettings(StrictRequestModel):
    """The only administrator-configurable retention values approved by ADR 0027."""

    importSourceRetentionDays: int = Field(default=365, strict=True, ge=30, le=730)
    agentConversationRetentionDays: int = Field(default=90, strict=True, ge=30, le=365)
    importRollbackProtectionDays: int = Field(default=30, strict=True, ge=7, le=90)


def is_utc(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def require_utc(value: datetime, *, field: str) -> datetime:
    if not is_utc(value):
        raise ValueError(f"{field} must be a UTC datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class FrozenRetentionConfiguration:
    """A release-scoped policy used only while creating a new frozen fact."""

    version: str
    settings: RetentionSettings

    def source_expires_at(self, created_at: datetime) -> datetime:
        return require_utc(created_at, field="created_at") + timedelta(
            days=self.settings.importSourceRetentionDays
        )

    def conversation_expires_at(self, created_at: datetime) -> datetime:
        return require_utc(created_at, field="created_at") + timedelta(
            days=self.settings.agentConversationRetentionDays
        )

    def rollback_protected_until(self, confirmed_at: datetime) -> datetime:
        return require_utc(confirmed_at, field="confirmed_at") + timedelta(
            days=self.settings.importRollbackProtectionDays
        )


__all__ = [
    "DEFAULT_RETENTION_CONFIG_VERSION",
    "FrozenRetentionConfiguration",
    "RetentionSettings",
    "is_utc",
    "require_utc",
]
