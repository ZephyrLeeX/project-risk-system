"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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


class MailSyncStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILURE = "FAILURE"


class MailboxConnectionStatus(StrEnum):
    UNTESTED = "UNTESTED"
    HEALTHY = "HEALTHY"
    FAILED = "FAILED"


class MailboxEncryption(StrEnum):
    SSL = "SSL"
    STARTTLS = "STARTTLS"


class MailboxProvider(StrEnum):
    QQ = "QQ"
    IMAP = "IMAP"


class MailboxConfig(Base):
    __tablename__ = "mailbox_configs"
    __table_args__ = (
        Index("mailbox_configs_enabled_autoSyncEnabled_idx", "enabled", "autoSyncEnabled"),
        Index("mailbox_configs_connectionStatus_idx", "connectionStatus"),
        Index("mailbox_configs_userId_key", "userId", unique=True),
        CheckConstraint('"imapPort" BETWEEN 1 AND 65535', name="port_check"),
        CheckConstraint('"initialSyncWeeks" IN (1, 4, 8, 12)', name="weeks_check"),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    userId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    provider: Mapped[MailboxProvider] = mapped_column(
        Enum(MailboxProvider, name="MailboxProvider", native_enum=True), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    imapHost: Mapped[str] = mapped_column(String(255), nullable=False)
    imapPort: Mapped[int] = mapped_column(Integer, nullable=False)
    encryption: Mapped[MailboxEncryption] = mapped_column(
        Enum(MailboxEncryption, name="MailboxEncryption", native_enum=True), nullable=False
    )
    folder: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("'INBOX'"))
    encryptedAuthCode: Mapped[str] = mapped_column(Text, nullable=False)
    authCodeIv: Mapped[str] = mapped_column(String(64), nullable=False)
    authCodeTag: Mapped[str] = mapped_column(String(64), nullable=False)
    authCodeLast4: Mapped[str] = mapped_column(String(16), nullable=False)
    subjectKeywords: Mapped[JSONValue] = mapped_column(JSONB, nullable=False)
    senderRule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    initialSyncWeeks: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("4"))
    readAttachments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    aiExtractionEnabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    autoSyncEnabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    uidCursor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    connectionStatus: Mapped[MailboxConnectionStatus] = mapped_column(
        Enum(MailboxConnectionStatus, name="MailboxConnectionStatus", native_enum=True),
        nullable=False,
        server_default=text("'UNTESTED'"),
    )
    lastTestAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    lastTestLatencyMs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lastTestErrorCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lastTestErrorSummary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lastSyncAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    lastSyncStatus: Mapped[MailSyncStatus | None] = mapped_column(
        Enum(MailSyncStatus, name="MailSyncStatus", native_enum=True), nullable=True
    )
    lastSyncNewCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lastSyncSuccessCount: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lastSyncRiskCandidateCount: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lastSyncFailedCount: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
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


class MailSyncTrigger(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    RETRY = "RETRY"


class MailSyncBatch(Base):
    __tablename__ = "mail_sync_batches"
    __table_args__ = (
        Index("mail_sync_batches_mailboxConfigId_createdAt_idx", "mailboxConfigId", "createdAt"),
        Index("mail_sync_batches_status_createdAt_idx", "status", "createdAt"),
        Index("mail_sync_batches_operatorUserId_idx", "operatorUserId"),
        Index("mail_sync_batches_retryOfId_idx", "retryOfId"),
        Index("mail_sync_batches_targetMessageId_idx", "targetMessageId"),
        Index("mail_sync_batches_code_key", "code", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    mailboxConfigId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mailbox_configs.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    trigger: Mapped[MailSyncTrigger] = mapped_column(
        Enum(MailSyncTrigger, name="MailSyncTrigger", native_enum=True), nullable=False
    )
    status: Mapped[MailSyncStatus] = mapped_column(
        Enum(MailSyncStatus, name="MailSyncStatus", native_enum=True),
        nullable=False,
        server_default=text("'QUEUED'"),
    )
    operatorUserId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    startedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    finishedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    durationMs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scannedCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    newCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    successCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skippedCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failedCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    riskCandidateCount: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    startUid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    endUid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    errorSummary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retryOfId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mail_sync_batches.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    targetMessageId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey(
            "mail_messages.id",
            ondelete="SET NULL",
            onupdate="CASCADE",
            use_alter=True,
        ),
        nullable=True,
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


class MailMessageSkipReason(StrEnum):
    DUPLICATE = "DUPLICATE"
    RULE_MISMATCH = "RULE_MISMATCH"


class MailMessageStatus(StrEnum):
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class MailMessage(Base):
    __tablename__ = "mail_messages"
    __table_args__ = (
        Index(
            "mail_messages_mailboxConfigId_imapUid_key", "mailboxConfigId", "imapUid", unique=True
        ),
        Index("mail_messages_mailboxConfigId_messageId_idx", "mailboxConfigId", "messageId"),
        Index("mail_messages_batchId_status_idx", "batchId", "status"),
        Index("mail_messages_mailboxConfigId_sentAt_idx", "mailboxConfigId", "sentAt"),
        Index("mail_messages_status_updatedAt_idx", "status", "updatedAt"),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    mailboxConfigId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mailbox_configs.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    batchId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mail_sync_batches.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    messageId: Mapped[str] = mapped_column(String(500), nullable=False)
    imapUid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    senderName: Mapped[str | None] = mapped_column(String(255), nullable=True)
    senderAddress: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sentAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    processedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    status: Mapped[MailMessageStatus] = mapped_column(
        Enum(MailMessageStatus, name="MailMessageStatus", native_enum=True),
        nullable=False,
        server_default=text("'ANALYZING'"),
    )
    skipReason: Mapped[MailMessageSkipReason | None] = mapped_column(
        Enum(MailMessageSkipReason, name="MailMessageSkipReason", native_enum=True), nullable=True
    )
    failureCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failureSummary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sanitizedSummary: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyPoints: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    attachmentMetadata: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    processingTrace: Mapped[JSONValue | None] = mapped_column(JSONB, nullable=True)
    retryCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
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


class MailProjectMatchType(StrEnum):
    EXACT = "EXACT"
    ALIAS = "ALIAS"
    FUZZY = "FUZZY"
    MANUAL = "MANUAL"


class MailMessageProjectMatch(Base):
    __tablename__ = "mail_message_project_matches"
    __table_args__ = (
        Index(
            "mail_message_project_matches_messageId_projectId_key",
            "messageId",
            "projectId",
            unique=True,
        ),
        Index("mail_message_project_matches_projectId_createdAt_idx", "projectId", "createdAt"),
        Index("mail_message_project_matches_confirmedById_idx", "confirmedById"),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    messageId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    projectId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    matchType: Mapped[MailProjectMatchType] = mapped_column(
        Enum(MailProjectMatchType, name="MailProjectMatchType", native_enum=True), nullable=False
    )
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    matchedText: Mapped[str] = mapped_column(String(500), nullable=False)
    confirmedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class MailRiskCandidateStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    IGNORED = "IGNORED"


class ProjectRiskLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class MailRiskCandidate(Base):
    __tablename__ = "mail_risk_candidates"
    __table_args__ = (
        Index("mail_risk_candidates_messageId_status_idx", "messageId", "status"),
        Index("mail_risk_candidates_projectId_status_idx", "projectId", "status"),
        Index("mail_risk_candidates_categoryId_status_idx", "categoryId", "status"),
        Index("mail_risk_candidates_reviewedById_idx", "reviewedById"),
        Index("mail_risk_candidates_confirmedRiskId_key", "confirmedRiskId", unique=True),
    )
    id: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, nullable=False, default=new_uuid
    )
    messageId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
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
    level: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[MailRiskCandidateStatus] = mapped_column(
        Enum(MailRiskCandidateStatus, name="MailRiskCandidateStatus", native_enum=True),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    confirmedRiskId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("risks.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    reviewedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    reviewedAt: Mapped[datetime | None] = mapped_column(
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
