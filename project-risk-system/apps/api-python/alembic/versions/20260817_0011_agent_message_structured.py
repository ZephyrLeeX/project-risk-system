"""Add the optional structured extension to retained Agent messages."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260817_0011"
down_revision: Final = "20260817_0010"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.add_column("agent_messages", sa.Column("structured", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("structured Agent message data is not destructively downgradable")
