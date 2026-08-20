"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import JSONValue, new_uuid
from risk_platform.models import Base

UUIDType = PG_UUID


class RiskTimelineEventType(StrEnum):
    RISK_CREATED = "RISK_CREATED"
    RISK_UPDATED = "RISK_UPDATED"
    LEVEL_CHANGED = "LEVEL_CHANGED"
    ACTION_CREATED = "ACTION_CREATED"
    ACTION_UPDATED = "ACTION_UPDATED"
    ACTION_STATUS_CHANGED = "ACTION_STATUS_CHANGED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    RISK_RESOLVED = "RISK_RESOLVED"
    RISK_REOPENED = "RISK_REOPENED"


class RiskTimelineEvent(Base):
    __tablename__ = "risk_timeline_events"
    __table_args__ = (
        Index("risk_timeline_events_projectId_occurredAt_idx", "projectId", "occurredAt"),
        Index("risk_timeline_events_riskId_occurredAt_idx", "riskId", "occurredAt"),
        Index("risk_timeline_events_actionItemId_idx", "actionItemId"),
        Index("risk_timeline_events_eventType_occurredAt_idx", "eventType", "occurredAt"),
        Index("risk_timeline_events_sourceBatchId_idx", "sourceBatchId"),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    projectId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    riskId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("risks.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    actionItemId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("action_items.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    eventType: Mapped[RiskTimelineEventType] = mapped_column(
        Enum(RiskTimelineEventType, name="RiskTimelineEventType", native_enum=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    fromValue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    toValue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actorUserId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    actorNameSource: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sourceBatchId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=True,
    )
    occurredAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    metadata_: Mapped[JSONValue | None] = mapped_column("metadata", JSONB, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
