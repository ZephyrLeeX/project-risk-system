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


class RiskLevelRule(Base):
    __tablename__ = "risk_level_rules"
    __table_args__ = (
        Index("risk_level_rules_isActive_sortOrder_idx", "isActive", "sortOrder"),
        Index("risk_level_rules_level_key", "level", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    level: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True), nullable=False
    )
    displayName: Mapped[str] = mapped_column(String(32), nullable=False)
    colorToken: Mapped[str] = mapped_column(String(16), nullable=False)
    criteria: Mapped[str] = mapped_column(String(500), nullable=False)
    keywords: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
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


class SystemConfigRelease(Base):
    __tablename__ = "system_config_releases"
    __table_args__ = (
        Index("system_config_releases_publishedAt_idx", "publishedAt"),
        Index("system_config_releases_module_publishedAt_idx", "module", "publishedAt"),
        Index("system_config_releases_version_key", "version", unique=True),
        Index("system_config_releases_traceId_key", "traceId", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    module: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'ALL'"))
    changeCount: Mapped[int] = mapped_column(Integer, nullable=False)
    changeSummary: Mapped[str] = mapped_column(String(500), nullable=False)
    impactScope: Mapped[JSONValue] = mapped_column(JSONB, nullable=False)
    beforeSnapshot: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    snapshot: Mapped[JSONValue] = mapped_column(JSONB, nullable=False)
    publishedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    traceId: Mapped[str] = mapped_column(String(64), nullable=False)
    publishedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
