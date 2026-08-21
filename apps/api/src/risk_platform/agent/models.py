"""Agent conversation, event, and confirmation persistence contract."""

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
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import JSONValue, new_uuid, utc_now
from risk_platform.models import Base

from .scope import ScopeRuleMatchType

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
    INTERACTION_REQUIRED = "interaction.required"
    INTERACTION_RESOLVED = "interaction.resolved"


class AgentExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentInteractionType(StrEnum):
    PROJECT_SELECTION = "PROJECT_SELECTION"
    WRITE_CONFIRMATION = "WRITE_CONFIRMATION"


class AgentInteractionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class AgentInteractionAction(StrEnum):
    SELECT = "SELECT"
    MANUAL_INPUT = "MANUAL_INPUT"
    CANCEL = "CANCEL"
    CONFIRM = "CONFIRM"


class MutationDraftOperation(StrEnum):
    RISK_CREATE = "risk_create_proposal"
    RISK_UPDATE = "risk_update_proposal"
    RISK_RESOLVE = "risk_resolve_proposal"
    TODO_CREATE = "todo_create_proposal"
    TODO_UPDATE = "todo_update_proposal"
    PROJECT_STATUS_UPDATE = "project_status_update_proposal"


class MutationDraftStatus(StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


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
        CheckConstraint(
            '"contextSummaryThroughSequence" >= 0',
            name="context_summary_through_nonnegative",
        ),
        CheckConstraint(
            '"contextSummaryVersion" >= 0', name="context_summary_version_nonnegative"
        ),
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
    contextSummary: Mapped[str | None] = mapped_column(Text)
    contextSummaryThroughSequence: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    contextSummaryVersion: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    contextUpdatedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3)
    )
    activeProjectId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL")
    )
    activeProjectName: Mapped[str | None] = mapped_column(String(255))
    # User-initiated "remove from my history" hide marker (ADR 0012/0018):
    # soft delete only — the durable fact graph (messages, events, executions,
    # interactions, drafts, tasks) stays intact for retention/audit, and the
    # retention cleanup worker still owns the physical lifecycle via expiresAt.
    deletedAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))


class AgentExecution(Base):
    __tablename__ = "agent_executions"
    __table_args__ = (
        Index("agent_executions_conversationId_status_idx", "conversationId", "status"),
        Index("agent_executions_taskId_key", "taskId", unique=True),
        CheckConstraint("jsonb_typeof(resumeContext) = 'object'", name="resume_context_object"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    conversationId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    taskId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("durable_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    userMessageId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("agent_messages.id", ondelete="RESTRICT"), nullable=False
    )
    requestedByUserId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[AgentExecutionStatus] = mapped_column(
        Enum(AgentExecutionStatus, name="AgentExecutionStatus", native_enum=True),
        nullable=False,
        server_default="RUNNING",
    )
    resumeContext: Mapped[dict[str, JSONValue]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False, default=utc_now, onupdate=utc_now
    )
    completedAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))


class AgentInteraction(Base):
    __tablename__ = "agent_interactions"
    __table_args__ = (
        Index("agent_interactions_ownerUserId_status_idx", "ownerUserId", "status"),
        Index("agent_interactions_conversationId_status_idx", "conversationId", "status"),
        Index("agent_interactions_executionId_idx", "executionId"),
        CheckConstraint("jsonb_typeof(candidateOptions) = 'array'", name="candidate_options_array"),
        CheckConstraint(
            "jsonb_typeof(resumeContext) = 'object'", name="interaction_context_object"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    executionId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversationId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    ownerUserId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[AgentInteractionType] = mapped_column(
        Enum(AgentInteractionType, name="AgentInteractionType", native_enum=True), nullable=False
    )
    status: Mapped[AgentInteractionStatus] = mapped_column(
        Enum(AgentInteractionStatus, name="AgentInteractionStatus", native_enum=True),
        nullable=False,
        server_default="OPEN",
    )
    candidateOptions: Mapped[list[JSONValue]] = mapped_column(JSONB, nullable=False)
    resumeContext: Mapped[dict[str, JSONValue]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    responseAction: Mapped[AgentInteractionAction | None] = mapped_column(
        Enum(AgentInteractionAction, name="AgentInteractionAction", native_enum=True)
    )
    responsePayload: Mapped[dict[str, JSONValue] | None] = mapped_column(JSONB)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expiresAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    resolvedAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))


class MutationDraft(Base):
    __tablename__ = "agent_mutation_drafts"
    __table_args__ = (
        Index("agent_mutation_drafts_ownerUserId_status_idx", "ownerUserId", "status"),
        Index("agent_mutation_drafts_interactionId_key", "interactionId", unique=True),
        Index("agent_mutation_drafts_idempotencyKey_key", "idempotencyKey", unique=True),
        CheckConstraint("jsonb_typeof(proposal) = 'object'", name="proposal_object"),
        CheckConstraint("btrim(digest) <> ''", name="digest_nonempty"),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    interactionId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_interactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ownerUserId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    conversationId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    executionId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[MutationDraftOperation] = mapped_column(
        Enum(
            MutationDraftOperation,
            name="MutationDraftOperation",
            native_enum=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    status: Mapped[MutationDraftStatus] = mapped_column(
        Enum(
            MutationDraftStatus,
            name="MutationDraftStatus",
            native_enum=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        server_default="OPEN",
    )
    proposal: Mapped[dict[str, JSONValue]] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    idempotencyKey: Mapped[str] = mapped_column(String(255), nullable=False)
    resultResourceType: Mapped[str | None] = mapped_column(String(64))
    resultResourceId: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    failureCode: Mapped[str | None] = mapped_column(String(64))
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expiresAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    resolvedAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))


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
    structured: Mapped[dict[str, JSONValue] | None] = mapped_column(JSONB, nullable=True)
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
        Index("agent_execution_configs_userMessageId_idx", "userMessageId"),
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


class AgentScopeRuleDecision(StrEnum):
    """Runtime scope rules only ever force-admit or force-reject.

    DEFER is the default for unmatched text and is deliberately not a rule
    decision: rules exist to encode *certain* business / non-business intent.
    """

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class AgentScopeRule(Base):
    """Admin-managed runtime layer-1 scope rule (PG is the source of truth).

    New rules are created disabled (``enabled = false``) so an admin can
    verify them with the /test endpoint before enforcement.  Deletion is
    soft (``deletedAt``) to keep the security-configuration history.
    """

    __tablename__ = "agent_scope_rules"
    __table_args__ = (
        Index("agent_scope_rules_enabled_idx", "enabled"),
        Index("agent_scope_rules_deletedAt_idx", "deletedAt"),
        # Name uniqueness only among live (non-deleted) rows so a soft-deleted
        # rule's name can be reused later.
        Index(
            "agent_scope_rules_name_active_key",
            "name",
            unique=True,
            postgresql_where=text('"deletedAt" IS NULL'),
        ),
        CheckConstraint('"priority" BETWEEN 0 AND 1000', name="agent_scope_rules_priority_range"),
        CheckConstraint('"version" >= 1', name="agent_scope_rules_version_positive"),
        CheckConstraint("btrim(\"pattern\") <> ''", name="agent_scope_rules_pattern_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[AgentScopeRuleDecision] = mapped_column(
        Enum(AgentScopeRuleDecision, name="AgentScopeRuleDecision", native_enum=True),
        nullable=False,
    )
    matchType: Mapped[ScopeRuleMatchType] = mapped_column(
        Enum(ScopeRuleMatchType, name="AgentScopeRuleMatchType", native_enum=True),
        nullable=False,
    )
    pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    description: Mapped[str | None] = mapped_column(String(500))
    createdBy: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    deletedAt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True, precision=3))
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


class AgentScopeRuleRevision(Base):
    """Single-row monotonically increasing revision for the rule cache.

    Every rule mutation increments ``revision`` inside its own transaction;
    API/worker caches compare this cheap single-row value to decide whether
    to reload, and the Redis invalidation event carries it as its payload.
    """

    __tablename__ = "agent_scope_rule_revision"
    __table_args__ = (
        CheckConstraint('"id" = 1', name="agent_scope_rule_revision_single_row"),
        CheckConstraint('"revision" >= 0', name="agent_scope_rule_revision_nonnegative"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


__all__ = [
    "AgentConfirmationOperation",
    "AgentConfirmationToken",
    "AgentConversation",
    "AgentEvent",
    "AgentEventType",
    "AgentExecutionConfig",
    "AgentMessage",
    "AgentMessageRole",
    "AgentScopeRule",
    "AgentScopeRuleDecision",
    "AgentScopeRuleRevision",
    "MutationDraft",
    "MutationDraftOperation",
    "MutationDraftStatus",
]
