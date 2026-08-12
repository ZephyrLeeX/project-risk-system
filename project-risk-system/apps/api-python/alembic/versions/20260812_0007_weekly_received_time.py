"""Add immutable mail envelope times required by weekly ownership."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260812_0007"
down_revision: Final = "20260812_0006"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    source = postgresql.ENUM(
        "IMAP_INTERNALDATE",
        "FIRST_DURABLE_OBSERVATION",
        name="MailReceivedAtSource",
    )
    source.create(op.get_bind(), checkfirst=True)
    source_column = postgresql.ENUM(
        "IMAP_INTERNALDATE",
        "FIRST_DURABLE_OBSERVATION",
        name="MailReceivedAtSource",
        create_type=False,
    )

    op.add_column(
        "mail_source_handoffs",
        sa.Column("sentAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
    )
    op.add_column(
        "mail_source_handoffs",
        sa.Column("receivedAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
    )
    op.add_column(
        "mail_source_handoffs", sa.Column("receivedAtSource", source_column, nullable=True)
    )
    op.add_column(
        "mail_messages",
        sa.Column("uidValidity", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "mail_messages",
        sa.Column("receivedAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
    )
    op.add_column(
        "mail_messages", sa.Column("receivedAtSource", source_column, nullable=True)
    )

    op.execute(
        """
        UPDATE mail_source_handoffs
        SET "sentAt" = NULL,
            "receivedAt" = date_trunc('milliseconds', "createdAt"),
            "receivedAtSource" = 'FIRST_DURABLE_OBSERVATION';

        UPDATE mail_messages AS message
        SET "sentAt" = NULL,
            "uidValidity" = handoff."uidValidity",
            "receivedAt" = handoff."receivedAt",
            "receivedAtSource" = handoff."receivedAtSource"
        FROM mail_source_handoffs AS handoff
        WHERE handoff."mailboxConfigId" = message."mailboxConfigId"
          AND handoff."batchId" = message."batchId"
          AND handoff."imapUid" = message."imapUid";

        UPDATE mail_messages
        SET "sentAt" = NULL,
            "uidValidity" = COALESCE(
                "uidValidity",
                (SELECT batch."uidValidity" FROM mail_sync_batches AS batch
                 WHERE batch.id = mail_messages."batchId")
            ),
            "receivedAt" = date_trunc('milliseconds', "createdAt"),
            "receivedAtSource" = 'FIRST_DURABLE_OBSERVATION'
        WHERE "receivedAt" IS NULL;
        """
    )

    for table in ("mail_source_handoffs", "mail_messages"):
        op.alter_column(table, "receivedAt", nullable=False)
        op.alter_column(table, "receivedAtSource", nullable=False)
    op.create_index(
        "mail_messages_mailboxConfigId_receivedAt_idx",
        "mail_messages",
        ["mailboxConfigId", "receivedAt"],
    )
    op.drop_index(
        "mail_messages_mailboxConfigId_imapUid_key", table_name="mail_messages"
    )
    op.create_index(
        "mail_messages_mailbox_uidValidity_uid_key",
        "mail_messages",
        ["mailboxConfigId", "uidValidity", "imapUid"],
        unique=True,
    )

    op.execute(
        """
        CREATE FUNCTION mail_envelope_times_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD."mailboxConfigId" IS DISTINCT FROM NEW."mailboxConfigId"
                OR OLD."uidValidity" IS DISTINCT FROM NEW."uidValidity"
                OR OLD."imapUid" IS DISTINCT FROM NEW."imapUid"
                OR OLD."sentAt" IS DISTINCT FROM NEW."sentAt"
                OR OLD."receivedAt" IS DISTINCT FROM NEW."receivedAt"
                OR OLD."receivedAtSource" IS DISTINCT FROM NEW."receivedAtSource"
            THEN
                RAISE EXCEPTION 'mail envelope time facts are immutable';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER mail_source_handoffs_envelope_times_guard
        BEFORE UPDATE ON mail_source_handoffs
        FOR EACH ROW EXECUTE FUNCTION mail_envelope_times_immutable();

        CREATE TRIGGER mail_messages_envelope_times_guard
        BEFORE UPDATE ON mail_messages
        FOR EACH ROW EXECUTE FUNCTION mail_envelope_times_immutable();
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "T027 immutable received-time facts are not destructively downgradable"
    )
