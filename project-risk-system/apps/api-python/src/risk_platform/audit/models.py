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
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import JSONValue, new_uuid
from risk_platform.models import Base

UUIDType = PG_UUID


class AuditResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("audit_logs_actorUserId_idx", "actorUserId"),
        Index("audit_logs_module_action_idx", "module", "action"),
        Index("audit_logs_resourceType_resourceId_idx", "resourceType", "resourceId"),
        Index("audit_logs_traceId_idx", "traceId"),
        Index("audit_logs_createdAt_idx", "createdAt"),
        Index("audit_logs_isSensitive_createdAt_idx", "isSensitive", "createdAt"),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    actorUserId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resourceType: Mapped[str] = mapped_column(String(128), nullable=False)
    resourceId: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[AuditResult] = mapped_column(
        Enum(AuditResult, name="AuditResult", native_enum=True), nullable=False
    )
    traceId: Mapped[str] = mapped_column(String(64), nullable=False)
    clientIp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    userAgent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    beforeSnapshot: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    afterSnapshot: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    errorCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    isSensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    previousHash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    integrityHash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
