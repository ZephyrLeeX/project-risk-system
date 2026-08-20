"""Add weekly-report-only mailbox filter config and FILTERED skip reason.

Introduces the deterministic IMAP-discover MailCandidateFilter (see
``risk_platform/mailbox/filtering.py``) configuration surface on
``mailbox_configs``: ``weeklyReportOnly`` (default TRUE) and
``senderAllowlist`` (default '[]'::jsonb). Non-candidate mail is skipped at
discovery and never enters ``mail_messages`` / project matching / AI risk
extraction. Extends ``MailMessageSkipReason`` with ``FILTERED`` for the
historical re-judge / hide strategy that marks already-synced non-weekly
``MailMessage`` rows without physical deletion.
"""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260820_0018"
down_revision: Final = "20260819_0017"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.add_column(
        "mailbox_configs",
        sa.Column(
            "weeklyReportOnly",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
    )
    op.add_column(
        "mailbox_configs",
        sa.Column(
            "senderAllowlist",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute("ALTER TYPE \"MailMessageSkipReason\" ADD VALUE IF NOT EXISTS 'FILTERED'")


def downgrade() -> None:
    raise NotImplementedError("Mailbox candidate filter config is not destructively downgradable")
