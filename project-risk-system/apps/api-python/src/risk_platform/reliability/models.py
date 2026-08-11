"""PostgreSQL-backed durable task and transactional outbox models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import JSONValue, new_uuid, utc_now
from risk_platform.models import Base

UUIDType = PG_UUID


class DurableTaskKind(StrEnum):
    IMPORT_PREVIEW = "IMPORT_PREVIEW"
    MAILBOX_SYNC = "MAILBOX_SYNC"
    MAIL_MESSAGE_RETRY = "MAIL_MESSAGE_RETRY"
    ATTACHMENT_PARSE = "ATTACHMENT_PARSE"
    MAIL_AI_REVIEW_PUBLISH = "MAIL_AI_REVIEW_PUBLISH"
    RETENTION_CLEANUP = "RETENTION_CLEANUP"
    WEEKLY_REPORT_REBUILD = "WEEKLY_REPORT_REBUILD"


class DurableTaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DurableTask(Base):
    __tablename__ = "durable_tasks"
    __table_args__ = (
        Index("durable_tasks_kind_idempotencyKey_key", "kind", "idempotencyKey", unique=True),
        Index("durable_tasks_status_nextRetryAt_idx", "status", "nextRetryAt"),
        Index("durable_tasks_status_leaseExpiresAt_idx", "status", "leaseExpiresAt"),
        CheckConstraint("btrim(\"idempotencyKey\") <> ''", name="idempotency_key_nonempty"),
        CheckConstraint(
            '"maxAttempts" > 0 AND "attemptCount" >= 0 '
            'AND "attemptCount" <= "maxAttempts"',
            name="attempt_count_bounds",
        ),
        CheckConstraint('"dispatchGeneration" >= 0', name="dispatch_generation_nonnegative"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint(
            "(status = 'RUNNING' AND \"leaseToken\" IS NOT NULL "
            "AND \"leaseOwner\" IS NOT NULL AND \"heartbeatAt\" IS NOT NULL "
            "AND \"leaseExpiresAt\" IS NOT NULL) OR "
            "(status <> 'RUNNING' AND \"leaseToken\" IS NULL "
            "AND \"leaseOwner\" IS NULL AND \"heartbeatAt\" IS NULL "
            "AND \"leaseExpiresAt\" IS NULL)",
            name="lease_state",
        ),
        CheckConstraint(
            '"leaseExpiresAt" IS NULL OR "leaseExpiresAt" > "heartbeatAt"',
            name="lease_expiry_after_heartbeat",
        ),
        CheckConstraint(
            "(status = 'RETRY_WAIT' AND \"nextRetryAt\" IS NOT NULL) OR "
            "(status <> 'RETRY_WAIT' AND \"nextRetryAt\" IS NULL)",
            name="retry_schedule_state",
        ),
        CheckConstraint(
            "(status IN ('SUCCEEDED', 'FAILED', 'CANCELLED') AND \"completedAt\" IS NOT NULL) OR "
            "(status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED') AND \"completedAt\" IS NULL)",
            name="completion_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    kind: Mapped[DurableTaskKind] = mapped_column(
        Enum(DurableTaskKind, name="DurableTaskKind", native_enum=True), nullable=False
    )
    status: Mapped[DurableTaskStatus] = mapped_column(
        Enum(DurableTaskStatus, name="DurableTaskStatus", native_enum=True),
        nullable=False,
        server_default=text("'QUEUED'"),
    )
    idempotencyKey: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, JSONValue]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    attemptCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    maxAttempts: Mapped[int] = mapped_column(Integer, nullable=False)
    nextRetryAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    leaseToken: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True), nullable=True)
    leaseOwner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    heartbeatAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    leaseExpiresAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    dispatchGeneration: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    failureCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failureSummary: Mapped[str | None] = mapped_column(Text, nullable=True)
    startedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
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
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class TaskOutbox(Base):
    __tablename__ = "task_outbox"
    __table_args__ = (
        Index(
            "task_outbox_taskId_dispatchGeneration_key",
            "taskId",
            "dispatchGeneration",
            unique=True,
        ),
        Index("task_outbox_publishedAt_createdAt_idx", "publishedAt", "createdAt"),
        CheckConstraint('"dispatchGeneration" > 0', name="dispatch_generation_positive"),
    )

    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    taskId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("durable_tasks.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    dispatchGeneration: Mapped[int] = mapped_column(Integer, nullable=False)
    publishedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = ["DurableTask", "DurableTaskKind", "DurableTaskStatus", "TaskOutbox"]
