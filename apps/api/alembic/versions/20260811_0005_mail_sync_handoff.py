"""Add the UID/UIDVALIDITY-only durable mail synchronization facts."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260811_0005"
down_revision: Final = "20260811_0004"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.add_column("mailbox_configs", sa.Column("uidValidity", sa.BigInteger(), nullable=True))
    op.add_column("mail_sync_batches", sa.Column("uidValidity", sa.BigInteger(), nullable=True))
    for name in (
        "discoveredCount",
        "handedOffCount",
        "downstreamPendingCount",
        "retryableFailedCount",
        "permanentlyFailedCount",
        "cursorAdvanced",
    ):
        column = sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))
        if name == "cursorAdvanced":
            column = sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("FALSE"))
        op.add_column("mail_sync_batches", column)
    op.create_table(
        "mail_source_handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailboxConfigId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batchId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parseTaskId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uidValidity", sa.BigInteger(), nullable=False),
        sa.Column("imapUid", sa.BigInteger(), nullable=False),
        sa.Column("messageId", sa.String(length=500), nullable=True),
        sa.Column(
            "envelopeMetadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fetchStatus",
            sa.Enum(
                "PENDING",
                "SUCCEEDED",
                "RETRYABLE_FAILURE",
                "PERMANENT_FAILURE",
                name="MailStageStatus",
            ),
            server_default=sa.text("'SUCCEEDED'"),
            nullable=False,
        ),
        sa.Column(
            "handoffStatus",
            sa.Enum(
                "PENDING",
                "SUCCEEDED",
                "RETRYABLE_FAILURE",
                "PERMANENT_FAILURE",
                name="MailStageStatus",
                create_type=False,
            ),
            server_default=sa.text("'SUCCEEDED'"),
            nullable=False,
        ),
        sa.Column(
            "parseStatus",
            sa.Enum(
                "PENDING",
                "SUCCEEDED",
                "RETRYABLE_FAILURE",
                "PERMANENT_FAILURE",
                name="MailStageStatus",
                create_type=False,
            ),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "aiReviewStatus",
            sa.Enum(
                "PENDING",
                "SUCCEEDED",
                "RETRYABLE_FAILURE",
                "PERMANENT_FAILURE",
                name="MailStageStatus",
                create_type=False,
            ),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("failureCode", sa.String(length=128), nullable=True),
        sa.Column("failureSummary", sa.String(length=500), nullable=True),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("updatedAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.ForeignKeyConstraint(
            ["mailboxConfigId"], ["mailbox_configs.id"], ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["batchId"], ["mail_sync_batches.id"], ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parseTaskId"], ["durable_tasks.id"], ondelete="RESTRICT", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parseTaskId"),
    )
    op.create_index(
        "mail_source_handoffs_mailbox_uidValidity_uid_key",
        "mail_source_handoffs",
        ["mailboxConfigId", "uidValidity", "imapUid"],
        unique=True,
    )
    op.create_index("mail_source_handoffs_batchId_idx", "mail_source_handoffs", ["batchId"])
    op.create_index(
        "mail_source_handoffs_messageId_idx",
        "mail_source_handoffs",
        ["mailboxConfigId", "messageId"],
    )


def downgrade() -> None:
    raise NotImplementedError("T024 migration is not destructively downgradable")
