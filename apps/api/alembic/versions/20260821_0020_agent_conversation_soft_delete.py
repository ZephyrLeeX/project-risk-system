"""Agent conversation user soft-delete (hide from history).

Adds ``agent_conversations.deletedAt`` — a user-initiated "remove from my
history" marker. It is a soft delete only: the durable fact graph (messages,
events, executions, interactions, mutation drafts, durable tasks) is untouched
and the retention cleanup worker (ADR 0012) still owns the physical lifecycle
via ``expiresAt``.
"""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260821_0020"
down_revision: Final = "20260820_0019"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("deletedAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
    )


def downgrade() -> None:
    op.drop_column("agent_conversations", "deletedAt")
