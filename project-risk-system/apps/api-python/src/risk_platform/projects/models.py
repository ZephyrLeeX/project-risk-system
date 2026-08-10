"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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


class ProjectStatus(StrEnum):
    DELIVERY = "DELIVERY"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("projects_externalCode_key", "externalCode", unique=True),
        Index("projects_name_idx", "name"),
        Index("projects_departmentId_idx", "departmentId"),
        Index("projects_managerId_idx", "managerId"),
        Index("projects_status_idx", "status"),
        Index("projects_importKey_key", "importKey", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    externalCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    importKey: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="ProjectStatus", native_enum=True),
        nullable=False,
        server_default=text("'DELIVERY'"),
    )
    departmentId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    managerId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    deliveryOwnerName: Mapped[str | None] = mapped_column(String(128), nullable=True)
    annualPlanAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    actualCollectedAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    remainingAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    monthlyCollections: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    monthAttributes: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    collectionRiskLevel: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True),
        nullable=False,
        server_default=text("'UNKNOWN'"),
    )
    collectionProgress: Mapped[str | None] = mapped_column(Text, nullable=True)
    lastImportedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    sourceVersion: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
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


class ProjectAlias(Base):
    __tablename__ = "project_aliases"
    __table_args__ = (
        Index("project_aliases_projectId_isActive_idx", "projectId", "isActive"),
        Index("project_aliases_normalizedAlias_key", "normalizedAlias", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    projectId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalizedAlias: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'系统管理员'")
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    isActive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    hitCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lastHitAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
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
