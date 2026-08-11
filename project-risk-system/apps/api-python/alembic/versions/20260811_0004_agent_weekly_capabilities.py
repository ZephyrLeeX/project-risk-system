"""Add approved Agent and weekly-report capability schemas."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260811_0004"
down_revision: Final = "20260811_0003"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    # ADR 0021 explicitly registers the aggregate rebuild in ADR 0018's shared task registry.
    # PostgreSQL enum values are additive; no durable-task row or existing constraint is rewritten.
    op.execute("ALTER TYPE \"DurableTaskKind\" ADD VALUE IF NOT EXISTS 'WEEKLY_REPORT_REBUILD'")

    op.create_table(
        "agent_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ownerUserId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(precision=3, timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("updatedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.Column("expiresAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.Column("lastMessageSequence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lastEventSequence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint('"lastMessageSequence" >= 0', name="last_message_sequence_nonnegative"),
        sa.CheckConstraint('"lastEventSequence" >= 0', name="last_event_sequence_nonnegative"),
        sa.ForeignKeyConstraint(
            ["ownerUserId"],
            ["users.id"],
            name="agent_conversations_ownerUserId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="agent_conversations_pkey"),
    )
    op.create_index(
        "agent_conversations_ownerUserId_expiresAt_idx",
        "agent_conversations",
        ["ownerUserId", "expiresAt"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversationId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "role", sa.Enum("USER", "ASSISTANT", "TOOL", name="AgentMessageRole"), nullable=False
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("traceId", sa.String(length=128), nullable=False),
        sa.Column("dataAsOf", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(precision=3, timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint('"sequence" > 0', name="sequence_positive"),
        sa.ForeignKeyConstraint(
            ["conversationId"],
            ["agent_conversations.id"],
            name="agent_messages_conversationId_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="agent_messages_pkey"),
    )
    op.create_index(
        "agent_messages_conversationId_sequence_key",
        "agent_messages",
        ["conversationId", "sequence"],
        unique=True,
    )
    op.create_index(
        "agent_messages_conversationId_createdAt_idx",
        "agent_messages",
        ["conversationId", "createdAt"],
    )

    op.create_table(
        "agent_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversationId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("messageId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taskId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "message.delta",
                "progress",
                "preview",
                "completed",
                "error",
                "heartbeat",
                name="AgentEventType",
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(precision=3, timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint('"sequence" > 0', name="sequence_positive"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        sa.ForeignKeyConstraint(
            ["conversationId"],
            ["agent_conversations.id"],
            name="agent_events_conversationId_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["messageId"],
            ["agent_messages.id"],
            name="agent_events_messageId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["taskId"],
            ["durable_tasks.id"],
            name="agent_events_taskId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="agent_events_pkey"),
    )
    op.create_index(
        "agent_events_conversationId_sequence_key",
        "agent_events",
        ["conversationId", "sequence"],
        unique=True,
    )
    op.create_index("agent_events_conversationId_id_idx", "agent_events", ["conversationId", "id"])
    op.create_index("agent_events_taskId_idx", "agent_events", ["taskId"])

    op.create_table(
        "agent_confirmation_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tokenDigest", sa.String(length=64), nullable=False),
        sa.Column("ownerUserId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversationId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "operation",
            sa.Enum("REPORT", "PROCESS", "RESOLVE", name="AgentConfirmationOperation"),
            nullable=False,
        ),
        sa.Column("canonicalContent", sa.Text(), nullable=False),
        sa.Column("contentDigest", sa.String(length=64), nullable=False),
        sa.Column("scopeDigest", sa.String(length=64), nullable=False),
        sa.Column("idempotencyKey", sa.String(length=255), nullable=False),
        sa.Column("issuedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.Column("expiresAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.Column("usedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True),
        sa.Column("resultResourceType", sa.String(length=64), nullable=True),
        sa.Column("resultResourceId", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("btrim(\"canonicalContent\") <> ''", name="canonical_content_nonempty"),
        sa.CheckConstraint("btrim(\"idempotencyKey\") <> ''", name="idempotency_key_nonempty"),
        sa.CheckConstraint('"expiresAt" > "issuedAt"', name="expiry_after_issue"),
        sa.CheckConstraint(
            '("resultResourceType" IS NULL AND "resultResourceId" IS NULL) OR '
            '("resultResourceType" IS NOT NULL AND "resultResourceId" IS NOT NULL)',
            name="result_resource_pair",
        ),
        sa.ForeignKeyConstraint(
            ["ownerUserId"],
            ["users.id"],
            name="agent_confirmation_tokens_ownerUserId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversationId"],
            ["agent_conversations.id"],
            name="agent_confirmation_tokens_conversationId_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="agent_confirmation_tokens_pkey"),
    )
    op.create_index(
        "agent_confirmation_tokens_tokenDigest_key",
        "agent_confirmation_tokens",
        ["tokenDigest"],
        unique=True,
    )
    op.create_index(
        "agent_confirmation_tokens_idempotencyKey_key",
        "agent_confirmation_tokens",
        ["idempotencyKey"],
        unique=True,
    )
    op.create_index(
        "agent_confirmation_tokens_ownerUserId_expiresAt_idx",
        "agent_confirmation_tokens",
        ["ownerUserId", "expiresAt"],
    )
    op.create_index(
        "agent_confirmation_tokens_conversationId_idx",
        "agent_confirmation_tokens",
        ["conversationId"],
    )

    op.create_table(
        "weekly_report_aggregates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weekStart", sa.Date(), nullable=False),
        sa.Column("projectId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("riskCount", sa.Integer(), nullable=False),
        sa.Column("riskLevelCounts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sourceRevision", sa.Integer(), nullable=False),
        sa.Column("stale", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("generatedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.Column(
            "freshnessDeadline", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False
        ),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(precision=3, timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("updatedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.CheckConstraint('EXTRACT(ISODOW FROM "weekStart") = 1', name="week_start_is_monday"),
        sa.CheckConstraint('"riskCount" >= 0', name="risk_count_nonnegative"),
        sa.CheckConstraint('"sourceRevision" > 0', name="source_revision_positive"),
        sa.CheckConstraint('"freshnessDeadline" > "generatedAt"', name="freshness_after_generated"),
        sa.CheckConstraint("jsonb_typeof(summary) = 'object'", name="summary_object"),
        sa.CheckConstraint(
            "jsonb_typeof(\"riskLevelCounts\") = 'object'", name="risk_level_counts_object"
        ),
        sa.ForeignKeyConstraint(
            ["projectId"],
            ["projects.id"],
            name="weekly_report_aggregates_projectId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="weekly_report_aggregates_pkey"),
    )
    op.create_index(
        "weekly_report_aggregates_weekStart_projectId_key",
        "weekly_report_aggregates",
        ["weekStart", "projectId"],
        unique=True,
    )
    op.create_index(
        "weekly_report_aggregates_stale_freshnessDeadline_idx",
        "weekly_report_aggregates",
        ["stale", "freshnessDeadline"],
    )

    op.create_table(
        "weekly_report_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregateId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sourceMailId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sourceCandidateId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("riskId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("todoId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sourceRevision", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "riskLevel",
            postgresql.ENUM(
                "HIGH", "MEDIUM", "LOW", "UNKNOWN", name="ProjectRiskLevel", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "riskStatus",
            postgresql.ENUM("ACTIVE", "RESOLVED", name="RiskStatus", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "todoStatus",
            postgresql.ENUM(
                "PENDING", "IN_PROGRESS", "COMPLETED", name="ActionItemStatus", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("occurredAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.CheckConstraint('"sourceRevision" > 0', name="source_revision_positive"),
        sa.ForeignKeyConstraint(
            ["aggregateId"],
            ["weekly_report_aggregates.id"],
            name="weekly_report_items_aggregateId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sourceMailId"],
            ["mail_messages.id"],
            name="weekly_report_items_sourceMailId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sourceCandidateId"],
            ["mail_risk_candidates.id"],
            name="weekly_report_items_sourceCandidateId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["riskId"],
            ["risks.id"],
            name="weekly_report_items_riskId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["todoId"],
            ["action_items.id"],
            name="weekly_report_items_todoId_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="weekly_report_items_pkey"),
    )
    op.create_index(
        "weekly_report_items_aggregateId_sourceMailId_sourceCandidateId_riskId_key",
        "weekly_report_items",
        ["aggregateId", "sourceMailId", "sourceCandidateId", "riskId"],
        unique=True,
    )
    op.create_index(
        "weekly_report_items_aggregateId_occurredAt_idx",
        "weekly_report_items",
        ["aggregateId", "occurredAt"],
    )

    op.execute("""
        CREATE FUNCTION agent_messages_assign_sequence() RETURNS trigger AS $$
        DECLARE expected_sequence integer;
        BEGIN
            UPDATE agent_conversations
            SET "lastMessageSequence" = "lastMessageSequence" + 1,
                "updatedAt" = CURRENT_TIMESTAMP
            WHERE id = NEW."conversationId"
            RETURNING "lastMessageSequence" INTO expected_sequence;
            IF expected_sequence IS NULL OR NEW.sequence <> expected_sequence THEN
                RAISE EXCEPTION 'agent message sequence must be contiguous';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER agent_messages_assign_sequence_trigger
        BEFORE INSERT ON agent_messages
        FOR EACH ROW EXECUTE FUNCTION agent_messages_assign_sequence();
    """)
    op.execute("""
        CREATE FUNCTION agent_events_assign_sequence() RETURNS trigger AS $$
        DECLARE expected_sequence integer;
        BEGIN
            UPDATE agent_conversations
            SET "lastEventSequence" = "lastEventSequence" + 1,
                "updatedAt" = CURRENT_TIMESTAMP
            WHERE id = NEW."conversationId"
            RETURNING "lastEventSequence" INTO expected_sequence;
            IF expected_sequence IS NULL OR NEW.sequence <> expected_sequence THEN
                RAISE EXCEPTION 'agent event sequence must be contiguous';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER agent_events_assign_sequence_trigger
        BEFORE INSERT ON agent_events
        FOR EACH ROW EXECUTE FUNCTION agent_events_assign_sequence();
    """)


def downgrade() -> None:
    raise NotImplementedError(
        "Agent 和周报能力 schema 不提供破坏性 downgrade; 请恢复备份或重建隔离数据库"
    )
