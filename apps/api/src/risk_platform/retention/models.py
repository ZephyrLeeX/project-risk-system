"""Auditable retention-hold persistence facts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import new_uuid
from risk_platform.models import Base

UUIDType = PG_UUID


class RetentionResourceType(StrEnum):
    IMPORT_BATCH = "IMPORT_BATCH"
    AGENT_CONVERSATION = "AGENT_CONVERSATION"
    BACKUP_COPY = "BACKUP_COPY"


class RetentionHoldReason(StrEnum):
    LEGAL = "LEGAL"
    INVESTIGATION = "INVESTIGATION"
    INCIDENT = "INCIDENT"
    RESTORE_DRILL = "RESTORE_DRILL"


class RetentionHoldStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class RetentionHold(Base):
    __tablename__ = "retention_holds"
    __table_args__ = (
        Index("retention_holds_resource_status_idx", "resourceType", "resourceId", "status"),
        Index("retention_holds_expiresAt_idx", "expiresAt"),
        Index(
            "retention_holds_active_resource_key",
            "resourceType",
            "resourceId",
            unique=True,
            postgresql_where=text("\"status\" = 'ACTIVE'"),
        ),
        CheckConstraint("btrim(\"resourceId\") <> ''", name="resource_id_nonempty"),
        CheckConstraint("btrim(\"createdTraceId\") <> ''", name="created_trace_nonempty"),
        CheckConstraint(
            '"expiresAt" IS NULL OR "expiresAt" > "createdAt"', name="expiry_after_creation"
        ),
        CheckConstraint(
            '("status" = \'ACTIVE\' AND "releasedAt" IS NULL AND "releasedById" IS NULL '
            'AND "releasedTraceId" IS NULL AND "expiredAt" IS NULL AND "expiredById" IS NULL '
            'AND "expiredTraceId" IS NULL) OR '
            '("status" = \'RELEASED\' AND "releasedAt" IS NOT NULL AND "releasedById" IS NOT NULL '
            'AND "releasedTraceId" IS NOT NULL AND "expiredAt" IS NULL AND "expiredById" IS NULL '
            'AND "expiredTraceId" IS NULL) OR '
            '("status" = \'EXPIRED\' AND "releasedAt" IS NULL AND "releasedById" IS NULL '
            'AND "releasedTraceId" IS NULL AND "expiredAt" IS NOT NULL '
            'AND "expiredTraceId" IS NOT NULL)',
            name="terminal_facts_match_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    resourceType: Mapped[RetentionResourceType] = mapped_column(
        Enum(RetentionResourceType, name="RetentionResourceType", native_enum=True), nullable=False
    )
    resourceId: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[RetentionHoldReason] = mapped_column(
        Enum(RetentionHoldReason, name="RetentionHoldReason", native_enum=True), nullable=False
    )
    status: Mapped[RetentionHoldStatus] = mapped_column(
        Enum(RetentionHoldStatus, name="RetentionHoldStatus", native_enum=True),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    createdById: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    createdTraceId: Mapped[str] = mapped_column(String(64), nullable=False)
    expiresAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))
    releasedAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))
    releasedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="CASCADE")
    )
    releasedTraceId: Mapped[str | None] = mapped_column(String(64))
    expiredAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))
    expiredById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT", onupdate="CASCADE")
    )
    expiredTraceId: Mapped[str | None] = mapped_column(String(64))


__all__ = [
    "RetentionHold",
    "RetentionHoldReason",
    "RetentionHoldStatus",
    "RetentionResourceType",
]
