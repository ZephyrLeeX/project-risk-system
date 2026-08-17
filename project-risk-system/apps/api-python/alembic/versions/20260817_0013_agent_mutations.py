"""Add confirmed mutation drafts and Risk 1:N Todo invariants."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260817_0013"
down_revision: Final = "20260817_0012"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.execute("ALTER TYPE \"RiskSourceType\" ADD VALUE IF NOT EXISTS 'AGENT'")
    op.add_column(
        "action_items",
        sa.Column(
            "isDefaultForRisk", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")
        ),
    )
    op.drop_index("action_items_riskId_key", table_name="action_items")
    op.create_index(
        "action_items_default_risk_key",
        "action_items",
        ["riskId"],
        unique=True,
        postgresql_where=sa.text('"riskId" IS NOT NULL AND "isDefaultForRisk" = TRUE'),
    )
    draft_operation = postgresql.ENUM(
        "risk_create_proposal",
        "risk_update_proposal",
        "risk_resolve_proposal",
        "todo_create_proposal",
        "todo_update_proposal",
        "project_status_update_proposal",
        name="MutationDraftOperation",
        create_type=False,
    )
    draft_status = postgresql.ENUM(
        "OPEN",
        "CONFIRMED",
        "CANCELLED",
        "EXPIRED",
        "FAILED",
        name="MutationDraftStatus",
        create_type=False,
    )
    draft_operation.create(op.get_bind(), checkfirst=True)
    draft_status.create(op.get_bind(), checkfirst=True)
    op.execute("ALTER TYPE \"AgentInteractionType\" ADD VALUE IF NOT EXISTS 'WRITE_CONFIRMATION'")
    op.execute("ALTER TYPE \"AgentInteractionAction\" ADD VALUE IF NOT EXISTS 'CONFIRM'")
    op.create_table(
        "agent_mutation_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interactionId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ownerUserId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversationId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("executionId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", draft_operation, nullable=False),
        sa.Column("status", draft_status, nullable=False, server_default="OPEN"),
        sa.Column("proposal", postgresql.JSONB, nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotencyKey", sa.String(255), nullable=False),
        sa.Column("resultResourceType", sa.String(64)),
        sa.Column("resultResourceId", postgresql.UUID(as_uuid=True)),
        sa.Column("failureCode", sa.String(64)),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expiresAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("resolvedAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
        sa.ForeignKeyConstraint(["interactionId"], ["agent_interactions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ownerUserId"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["conversationId"], ["agent_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["executionId"], ["agent_executions.id"], ondelete="CASCADE"),
        sa.CheckConstraint("jsonb_typeof(proposal) = 'object'", name="proposal_object"),
        sa.CheckConstraint("btrim(digest) <> ''", name="digest_nonempty"),
    )
    op.create_index(
        "agent_mutation_drafts_ownerUserId_status_idx",
        "agent_mutation_drafts",
        ["ownerUserId", "status"],
    )
    op.create_index(
        "agent_mutation_drafts_interactionId_key",
        "agent_mutation_drafts",
        ["interactionId"],
        unique=True,
    )
    op.create_index(
        "agent_mutation_drafts_idempotencyKey_key",
        "agent_mutation_drafts",
        ["idempotencyKey"],
        unique=True,
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Agent mutation data and enum extensions are not destructively downgradable"
    )
