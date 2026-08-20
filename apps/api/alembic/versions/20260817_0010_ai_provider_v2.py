"""Add AI Provider V2 account/model configuration tables."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260817_0010"
down_revision: Final = "20260816_0009"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    provider_type = postgresql.ENUM(
        "DEEPSEEK_OFFICIAL", name="AiProviderType", create_type=False
    )
    account_health = postgresql.ENUM(
        "UNTESTED",
        "AVAILABLE",
        "CREDENTIAL_ERROR",
        name="AiProviderAccountHealth",
        create_type=False,
    )
    model_health = postgresql.ENUM(
        "UNTESTED", "AVAILABLE", "CONFIG_ERROR", name="AiModelHealth", create_type=False
    )
    provider_type.create(op.get_bind(), checkfirst=True)
    account_health.create(op.get_bind(), checkfirst=True)
    model_health.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ai_provider_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("providerType", provider_type, nullable=False),
        sa.Column("encryptedApiKey", sa.Text(), nullable=False),
        sa.Column("keyLast4", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column(
            "health", account_health, server_default=sa.text("'UNTESTED'"), nullable=False
        ),
        sa.Column("lastHealthAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
        sa.Column("lastHealthErrorCode", sa.String(64)),
        sa.Column("createdById", postgresql.UUID(as_uuid=True)),
        sa.Column("updatedById", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["createdById"], ["users.id"], ondelete="SET NULL", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updatedById"], ["users.id"], ondelete="SET NULL", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ai_provider_accounts_name_key", "ai_provider_accounts", ["name"], unique=True)
    op.create_index(
        "ai_provider_accounts_enabled_health_idx",
        "ai_provider_accounts",
        ["enabled", "health"],
    )

    op.create_table(
        "ai_model_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accountId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("modelName", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("isDefault", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("timeoutSeconds", sa.Integer(), server_default=sa.text("60"), nullable=False),
        sa.Column("health", model_health, server_default=sa.text("'UNTESTED'"), nullable=False),
        sa.Column("lastHealthAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
        sa.Column("lastHealthErrorCode", sa.String(64)),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["accountId"], ["ai_provider_accounts.id"], ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accountId", "modelName", name="ai_model_configs_account_model_key"),
    )
    op.create_index(
        "ai_model_configs_candidate_idx",
        "ai_model_configs",
        ["enabled", "health", "isDefault", "priority", "id"],
    )
    op.create_index(
        "ai_model_configs_one_enabled_default_per_account_key",
        "ai_model_configs",
        ["accountId"],
        unique=True,
        postgresql_where=sa.text('"isDefault" = TRUE AND enabled = TRUE'),
    )

    op.create_table(
        "ai_provider_v2_call_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accountId", postgresql.UUID(as_uuid=True)),
        sa.Column("modelConfigId", postgresql.UUID(as_uuid=True)),
        sa.Column("accountNameSnapshot", sa.String(128), nullable=False),
        sa.Column("modelNameSnapshot", sa.String(128), nullable=False),
        sa.Column("httpStatus", sa.Integer()),
        sa.Column("durationMs", sa.Integer(), nullable=False),
        sa.Column("inputTokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("outputTokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("totalTokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "result",
            postgresql.ENUM(name="AiCallResult", create_type=False),
            nullable=False,
        ),
        sa.Column("errorClassification", sa.String(64)),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["accountId"], ["ai_provider_accounts.id"], ondelete="SET NULL", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["modelConfigId"], ["ai_model_configs.id"], ondelete="SET NULL", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ai_provider_v2_call_logs_account_created_idx",
        "ai_provider_v2_call_logs",
        ["accountId", "createdAt"],
    )
    op.create_index(
        "ai_provider_v2_call_logs_model_created_idx",
        "ai_provider_v2_call_logs",
        ["modelConfigId", "createdAt"],
    )


def downgrade() -> None:
    op.drop_table("ai_provider_v2_call_logs")
    op.drop_table("ai_model_configs")
    op.drop_table("ai_provider_accounts")
    postgresql.ENUM(name="AiModelHealth").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="AiProviderAccountHealth").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="AiProviderType").drop(op.get_bind(), checkfirst=True)
