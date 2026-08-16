"""Agent conversation, event, and confirmation persistence contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
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


class AgentMessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"


class AgentEventType(StrEnum):
    MESSAGE_DELTA = "message.delta"
    PROGRESS = "progress"
    PREVIEW = "preview"
    COMPLETED = "completed"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


class AgentConfirmationOperation(StrEnum):
    REPORT = "REPORT"
    PROCESS = "PROCESS"
    RESOLVE = "RESOLVE"


class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        Index("agent_conversations_ownerUserId_expiresAt_idx", "ownerUserId", "expiresAt"),
        CheckConstraint('"lastMessageSequence" >= 0', name="last_message_sequence_nonnegative"),
        CheckConstraint('"lastEventSequence" >= 0', name="last_event_sequence_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    ownerUserId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
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
    expiresAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    retentionConfigVersion: Mapped[str] = mapped_column(String(32), nullable=False)
    lastMessageSequence: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lastEventSequence: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        Index(
            "agent_messages_conversationId_sequence_key", "conversationId", "sequence", unique=True
        ),
        Index("agent_messages_conversationId_createdAt_idx", "conversationId", "createdAt"),
        CheckConstraint('"sequence" > 0', name="sequence_positive"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    conversationId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[AgentMessageRole] = mapped_column(
        Enum(AgentMessageRole, name="AgentMessageRole", native_enum=True), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    traceId: Mapped[str] = mapped_column(String(128), nullable=False)
    dataAsOf: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        Index(
            "agent_events_conversationId_sequence_key", "conversationId", "sequence", unique=True
        ),
        Index("agent_events_conversationId_id_idx", "conversationId", "id"),
        Index("agent_events_taskId_idx", "taskId"),
        CheckConstraint('"sequence" > 0', name="sequence_positive"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    conversationId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    messageId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_messages.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    taskId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("durable_tasks.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[AgentEventType] = mapped_column(
        Enum(
            AgentEventType,
            name="AgentEventType",
            native_enum=True,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, JSONValue]] = mapped_column(JSONB, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AgentExecutionConfig(Base):
    """Immutable, PostgreSQL-owned Provider configuration for one execution."""

    __tablename__ = "agent_execution_configs"
    __table_args__ = (
        Index("agent_execution_configs_taskId_key", "taskId", unique=True),
        Index("agent_execution_configs_userMessageId_key", "userMessageId", unique=True),
        CheckConstraint(
            '("providerConfigId" IS NULL AND "providerNameSnapshot" IS NULL '
            'AND "endpointSnapshot" IS NULL AND "modelSnapshot" IS NULL '
            'AND "encryptedApiKeySnapshot" IS NULL) OR '
            '("providerConfigId" IS NOT NULL AND "providerNameSnapshot" IS NOT NULL '
            'AND "endpointSnapshot" IS NOT NULL AND "modelSnapshot" IS NOT NULL '
            'AND "encryptedApiKeySnapshot" IS NOT NULL)',
            name="provider_snapshot_pair",
        ),
        CheckConstraint('"timeoutSeconds" = 90', name="timeout_seconds_fixed"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    taskId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("durable_tasks.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    conversationId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    userMessageId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_messages.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    requestedByUserId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    providerConfigId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("ai_provider_configs.id", ondelete="RESTRICT", onupdate="CASCADE"),
    )
    providerNameSnapshot: Mapped[str | None] = mapped_column(String(128))
    endpointSnapshot: Mapped[str | None] = mapped_column(String(500))
    protocolSnapshot: Mapped[str | None] = mapped_column(String(32))
    modelSnapshot: Mapped[str | None] = mapped_column(String(128))
    encryptedApiKeySnapshot: Mapped[str | None] = mapped_column(Text)
    timeoutSeconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("90"))
    cancellationRequestedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3)
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AgentConfirmationToken(Base):
    __tablename__ = "agent_confirmation_tokens"
    __table_args__ = (
        Index("agent_confirmation_tokens_tokenDigest_key", "tokenDigest", unique=True),
        Index("agent_confirmation_tokens_idempotencyKey_key", "idempotencyKey", unique=True),
        Index("agent_confirmation_tokens_ownerUserId_expiresAt_idx", "ownerUserId", "expiresAt"),
        Index("agent_confirmation_tokens_conversationId_idx", "conversationId"),
        CheckConstraint("btrim(\"canonicalContent\") <> ''", name="canonical_content_nonempty"),
        CheckConstraint("btrim(\"idempotencyKey\") <> ''", name="idempotency_key_nonempty"),
        CheckConstraint('"expiresAt" > "issuedAt"', name="expiry_after_issue"),
        CheckConstraint(
            '("resultResourceType" IS NULL AND "resultResourceId" IS NULL) OR '
            '("resultResourceType" IS NOT NULL AND "resultResourceId" IS NOT NULL)',
            name="result_resource_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    tokenDigest: Mapped[str] = mapped_column(String(64), nullable=False)
    ownerUserId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    conversationId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    operation: Mapped[AgentConfirmationOperation] = mapped_column(
        Enum(AgentConfirmationOperation, name="AgentConfirmationOperation", native_enum=True),
        nullable=False,
    )
    canonicalContent: Mapped[str] = mapped_column(Text, nullable=False)
    contentDigest: Mapped[str] = mapped_column(String(64), nullable=False)
    scopeDigest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotencyKey: Mapped[str] = mapped_column(String(255), nullable=False)
    issuedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    expiresAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    usedAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))
    resultResourceType: Mapped[str | None] = mapped_column(String(64))
    resultResourceId: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))


__all__ = [
    "AgentConfirmationOperation",
    "AgentConfirmationToken",
    "AgentConversation",
    "AgentEvent",
    "AgentEventType",
    "AgentExecutionConfig",
    "AgentMessage",
    "AgentMessageRole",
]
