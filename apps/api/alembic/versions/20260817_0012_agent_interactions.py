"""Persist Agent business execution state and project-selection interactions."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260817_0012"
down_revision: Final = "20260817_0011"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.execute("ALTER TYPE \"AgentEventType\" ADD VALUE IF NOT EXISTS 'interaction.required'")
    op.execute("ALTER TYPE \"AgentEventType\" ADD VALUE IF NOT EXISTS 'interaction.resolved'")
    execution_status = postgresql.ENUM(
        "RUNNING",
        "WAITING_FOR_USER",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        name="AgentExecutionStatus",
        create_type=False,
    )
    interaction_type = postgresql.ENUM(
        "PROJECT_SELECTION", name="AgentInteractionType", create_type=False
    )
    interaction_status = postgresql.ENUM(
        "OPEN",
        "RESOLVED",
        "CANCELLED",
        "EXPIRED",
        name="AgentInteractionStatus",
        create_type=False,
    )
    interaction_action = postgresql.ENUM(
        "SELECT",
        "MANUAL_INPUT",
        "CANCEL",
        name="AgentInteractionAction",
        create_type=False,
    )
    for enum in (execution_status, interaction_type, interaction_status, interaction_action):
        enum.create(op.get_bind(), checkfirst=True)
    op.drop_index("agent_execution_configs_userMessageId_key", table_name="agent_execution_configs")
    op.create_index(
        "agent_execution_configs_userMessageId_idx", "agent_execution_configs", ["userMessageId"]
    )
    op.create_table(
        "agent_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversationId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taskId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("userMessageId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requestedByUserId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", execution_status, nullable=False, server_default="RUNNING"),
        sa.Column(
            "resumeContext", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updatedAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
        ),
        sa.Column("completedAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
        sa.ForeignKeyConstraint(["conversationId"], ["agent_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["taskId"], ["durable_tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["userMessageId"], ["agent_messages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requestedByUserId"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "jsonb_typeof(\"resumeContext\") = 'object'", name="resume_context_object"
        ),
    )
    op.create_index("agent_executions_taskId_key", "agent_executions", ["taskId"], unique=True)
    op.create_index(
        "agent_executions_conversationId_status_idx",
        "agent_executions",
        ["conversationId", "status"],
    )
    op.create_table(
        "agent_interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("executionId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversationId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ownerUserId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", interaction_type, nullable=False),
        sa.Column("status", interaction_status, nullable=False, server_default="OPEN"),
        sa.Column("candidateOptions", postgresql.JSONB, nullable=False),
        sa.Column(
            "resumeContext", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("responseAction", interaction_action),
        sa.Column("responsePayload", postgresql.JSONB),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expiresAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("resolvedAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
        sa.ForeignKeyConstraint(["executionId"], ["agent_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversationId"], ["agent_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ownerUserId"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "jsonb_typeof(\"candidateOptions\") = 'array'", name="candidate_options_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(\"resumeContext\") = 'object'", name="interaction_context_object"
        ),
    )
    op.create_index(
        "agent_interactions_ownerUserId_status_idx", "agent_interactions", ["ownerUserId", "status"]
    )
    op.create_index(
        "agent_interactions_conversationId_status_idx",
        "agent_interactions",
        ["conversationId", "status"],
    )
    op.create_index("agent_interactions_executionId_idx", "agent_interactions", ["executionId"])


def downgrade() -> None:
    raise NotImplementedError("Agent interaction data is not destructively downgradable")
