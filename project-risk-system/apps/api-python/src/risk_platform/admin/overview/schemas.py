"""Public contracts fixed by ADR 0023."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer

from risk_platform.audit.schemas import AuditModuleKey


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetimes(self, value: object) -> object:
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return value


class OverviewLink(_Contract):
    path: str
    query: dict[str, UUID]


class HealthItem(_Contract):
    key: Literal["API", "DATABASE", "REDIS", "WORKER", "AI_PROVIDER"]
    label: str
    status: Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"]
    checkedAt: datetime
    summary: str
    code: (
        Literal["TIMEOUT", "UNREACHABLE", "NO_ACTIVE_WORKER", "NO_ENABLED_PROVIDER", "CHECK_FAILED"]
        | None
    )
    link: OverviewLink | None


class AttentionItem(_Contract):
    id: str
    kind: Literal["IMPORT_REVIEW", "AI_PROVIDER_CONNECTION", "AI_PROVIDER_EXPIRY"]
    status: Literal["CRITICAL", "WARNING"]
    title: str
    summary: str
    occurredAt: datetime
    link: OverviewLink


class RecentAuditItem(_Contract):
    id: UUID
    occurredAt: datetime
    actorName: str
    module: AuditModuleKey
    action: str
    summary: str
    result: Literal["SUCCESS", "FAILURE"]
    resourceType: str
    resourceId: str | None
    traceId: str
    link: OverviewLink


class UnavailableSection(_Contract):
    section: Literal["health", "attention", "recentAudit"]
    reason: Literal["FORBIDDEN", "TIMEOUT", "DEPENDENCY_FAILURE"]
    code: Literal["FORBIDDEN", "TIMEOUT", "DEPENDENCY_FAILURE"]


class AdminOverview(_Contract):
    generatedAt: datetime
    health: list[HealthItem] | None
    attention: list[AttentionItem] | None
    recentAudit: list[RecentAuditItem] | None
    unavailableSections: list[UnavailableSection]


__all__ = [
    "AdminOverview",
    "AttentionItem",
    "HealthItem",
    "OverviewLink",
    "RecentAuditItem",
    "UnavailableSection",
]
