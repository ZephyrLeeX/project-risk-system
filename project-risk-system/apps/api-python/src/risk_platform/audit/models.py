"""Metadata-only append-only audit persistence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Enum,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import new_uuid
from risk_platform.models import Base

UUIDType = PG_UUID


class AuditResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class AuditActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    WORKER = "WORKER"
    AGENT = "AGENT"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("audit_logs_actorUserId_idx", "actorUserId"),
        Index("audit_logs_module_action_idx", "module", "action"),
        Index("audit_logs_resourceType_resourceId_idx", "resourceType", "resourceId"),
        Index("audit_logs_traceId_idx", "traceId"),
        Index("audit_logs_requestId_idx", "requestId"),
        Index("audit_logs_projectId_idx", "projectId"),
        Index("audit_logs_createdAt_idx", "createdAt"),
        Index("audit_logs_integrityHash_key", "integrityHash", unique=True),
        Index("audit_logs_previousHash_key", "previousHash", unique=True),
        CheckConstraint(
            '"module" ~ \'^[A-Z][A-Z0-9_.:-]{0,63}$\'',
            name="module_code",
        ),
        CheckConstraint(
            '"action" ~ \'^[A-Z][A-Z0-9_.:-]{0,127}$\'',
            name="action_code",
        ),
        CheckConstraint(
            '"resourceType" ~ \'^[A-Z][A-Z0-9_.:-]{0,127}$\'',
            name="resource_type_code",
        ),
        CheckConstraint(
            '"resourceId" IS NULL OR "resourceId" '
            "~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$'",
            name="resource_id",
        ),
        CheckConstraint(
            '"failureCode" IS NULL OR "failureCode" '
            "~ '^[A-Z][A-Z0-9_.:-]{0,127}$'",
            name="failure_code",
        ),
        CheckConstraint(
            '"actorType" NOT IN (\'USER\', \'AGENT\') OR "actorUserId" IS NOT NULL',
            name="actor_identity",
        ),
        CheckConstraint(
            '"traceId" '
            "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="trace_id",
        ),
        CheckConstraint(
            '"requestId" IS NULL OR "requestId" '
            "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="request_id",
        ),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    actorUserId: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True), nullable=True)
    actorType: Mapped[AuditActorType] = mapped_column(
        Enum(AuditActorType, name="AuditActorType", native_enum=True), nullable=False
    )
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resourceType: Mapped[str] = mapped_column(String(128), nullable=False)
    resourceId: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[AuditResult] = mapped_column(
        Enum(AuditResult, name="AuditResult", native_enum=True), nullable=False
    )
    traceId: Mapped[str] = mapped_column(String(64), nullable=False)
    requestId: Mapped[str | None] = mapped_column(String(64), nullable=True)
    projectId: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True), nullable=True)
    failureCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previousHash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    integrityHash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = ["AuditActorType", "AuditLog", "AuditResult"]
