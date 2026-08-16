"""Add explicit AI provider transport protocol."""

from typing import Final

import sqlalchemy as sa
from alembic import op

revision: Final = "20260816_0009"
down_revision: Final = "20260812_0008"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    protocol = sa.Enum(
        "OPENAI_CHAT_COMPLETIONS",
        "OPENAI_RESPONSES",
        "ANTHROPIC_MESSAGES",
        name="AiProviderProtocol",
    )
    protocol.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "ai_provider_configs",
        sa.Column("protocol", protocol, nullable=False, server_default="OPENAI_CHAT_COMPLETIONS"),
    )
    op.alter_column("ai_provider_configs", "protocol", server_default=None)
    op.add_column(
        "agent_execution_configs",
        sa.Column("protocolSnapshot", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_execution_configs", "protocolSnapshot")
    op.drop_column("ai_provider_configs", "protocol")
    sa.Enum(name="AiProviderProtocol").drop(op.get_bind(), checkfirst=True)
