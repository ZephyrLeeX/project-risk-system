"""Persist bounded mailbox project resolution state."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260818_0014"
down_revision: Final = "20260817_0013"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "PENDING", "AUTO_MATCH", "WAITING_CONFIRMATION", "CONFIRMED",
        name="MailProjectResolutionStatus", create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "mail_messages",
        sa.Column("projectResolutionStatus", status, nullable=False, server_default="PENDING"),
    )
    op.add_column("mail_messages", sa.Column("resolvedProjectId", postgresql.UUID(as_uuid=True)))
    op.add_column("mail_messages", sa.Column("projectResolutionCandidates", postgresql.JSONB))
    op.add_column("mail_messages", sa.Column("projectResolutionConfidence", sa.Integer()))
    op.add_column(
        "mail_messages",
        sa.Column("projectResolutionConfirmedById", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "mail_messages_resolvedProjectId_fkey", "mail_messages", "projects",
        ["resolvedProjectId"], ["id"], ondelete="SET NULL", onupdate="CASCADE",
    )
    op.create_foreign_key(
        "mail_messages_projectResolutionConfirmedById_fkey", "mail_messages", "users",
        ["projectResolutionConfirmedById"], ["id"], ondelete="SET NULL", onupdate="CASCADE",
    )
    op.create_index(
        "mail_messages_projectResolutionStatus_idx", "mail_messages", ["projectResolutionStatus"]
    )


def downgrade() -> None:
    raise NotImplementedError("Mailbox resolution state is not destructively downgradable")
