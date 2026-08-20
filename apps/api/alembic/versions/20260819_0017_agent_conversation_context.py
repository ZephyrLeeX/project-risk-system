"""Persist bounded Agent conversation memory and server-owned project grounding."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260819_0017"
down_revision: Final = "20260818_0016"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.add_column("agent_conversations", sa.Column("contextSummary", sa.Text()))
    op.add_column(
        "agent_conversations",
        sa.Column(
            "contextSummaryThroughSequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "agent_conversations",
        sa.Column(
            "contextSummaryVersion",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "agent_conversations",
        sa.Column("contextUpdatedAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
    )
    op.add_column(
        "agent_conversations",
        sa.Column("activeProjectId", postgresql.UUID(as_uuid=True)),
    )
    op.add_column("agent_conversations", sa.Column("activeProjectName", sa.String(255)))
    op.create_foreign_key(
        "agent_conversations_activeProjectId_fkey",
        "agent_conversations",
        "projects",
        ["activeProjectId"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "context_summary_through_nonnegative",
        "agent_conversations",
        '"contextSummaryThroughSequence" >= 0',
    )
    op.create_check_constraint(
        "context_summary_version_nonnegative",
        "agent_conversations",
        '"contextSummaryVersion" >= 0',
    )


def downgrade() -> None:
    raise NotImplementedError("Agent conversation memory is not destructively downgradable")
