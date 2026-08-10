"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.models import Base

UUIDType = PG_UUID


class ActionItemSourceType(StrEnum):
    RISK_SUGGESTION = "RISK_SUGGESTION"
    MANUAL = "MANUAL"


class ActionItemStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class ActionItemUrgency(StrEnum):
    EMERGENCY = "EMERGENCY"
    HIGH = "HIGH"
    NORMAL = "NORMAL"


class ActionItem(Base):
    __tablename__ = "action_items"
    __table_args__ = (
        Index("action_items_riskId_key", "riskId", unique=True),
        Index("action_items_projectId_status_idx", "projectId", "status"),
        Index("action_items_assigneeUserId_status_idx", "assigneeUserId", "status"),
        Index("action_items_assigneeNameSource_status_idx", "assigneeNameSource", "status"),
        Index("action_items_urgency_status_idx", "urgency", "status"),
        Index("action_items_dueDate_idx", "dueDate"),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    riskId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("risks.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=True,
    )
    projectId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[ActionItemUrgency] = mapped_column(
        Enum(ActionItemUrgency, name="ActionItemUrgency", native_enum=True), nullable=False
    )
    status: Mapped[ActionItemStatus] = mapped_column(
        Enum(ActionItemStatus, name="ActionItemStatus", native_enum=True),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    sourceType: Mapped[ActionItemSourceType] = mapped_column(
        Enum(ActionItemSourceType, name="ActionItemSourceType", native_enum=True),
        nullable=False,
        server_default=text("'RISK_SUGGESTION'"),
    )
    assigneeUserId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    assigneeNameSource: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dueDate: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    completionNote: Mapped[str | None] = mapped_column(Text, nullable=True)
    createdById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    completedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    completedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
