"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import JSONValue, new_uuid, utc_now
from risk_platform.models import Base

UUIDType = PG_UUID


class ProjectRiskLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class RiskCategory(Base):
    __tablename__ = "risk_categories"
    __table_args__ = (
        Index("risk_categories_isActive_sortOrder_idx", "isActive", "sortOrder"),
        Index("risk_categories_code_key", "code", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    keywords: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    colorToken: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'#4C8FE8'")
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    defaultLevel: Mapped[ProjectRiskLevel | None] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True), nullable=True
    )
    sortOrder: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    isActive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class RiskSourceType(StrEnum):
    EXCEL = "EXCEL"
    LITIGATION = "LITIGATION"
    MAIL_AI = "MAIL_AI"
    MANUAL = "MANUAL"


class RiskStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"


class Risk(Base):
    __tablename__ = "risks"
    __table_args__ = (
        Index("risks_projectId_status_idx", "projectId", "status"),
        Index("risks_categoryId_status_idx", "categoryId", "status"),
        Index("risks_level_status_idx", "level", "status"),
        Index("risks_sourceBatchId_idx", "sourceBatchId"),
        Index("risks_detectedAt_idx", "detectedAt"),
        Index("risks_dedupeFingerprint_key", "dedupeFingerprint", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    projectId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    categoryId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("risk_categories.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True), nullable=False
    )
    status: Mapped[RiskStatus] = mapped_column(
        Enum(RiskStatus, name="RiskStatus", native_enum=True),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    sourceType: Mapped[RiskSourceType] = mapped_column(
        Enum(RiskSourceType, name="RiskSourceType", native_enum=True), nullable=False
    )
    sourceBatchId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    sourceRefId: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True), nullable=True)
    reporterUserId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    reporterNameSource: Mapped[str | None] = mapped_column(String(100), nullable=True)
    weekCode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    detectedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    resolvedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    resolvedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    resolutionReason: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupeFingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
