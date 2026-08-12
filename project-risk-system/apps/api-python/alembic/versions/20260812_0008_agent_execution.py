"""Add ADR 0028 Agent execution facts."""
# ruff: noqa: E501

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260812_0008"
down_revision: Final = "20260812_0007"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.execute('ALTER TYPE "DurableTaskKind" ADD VALUE IF NOT EXISTS \'AGENT_EXECUTION\'')
    op.create_table(
        "agent_execution_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taskId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversationId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("userMessageId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requestedByUserId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("providerConfigId", postgresql.UUID(as_uuid=True)),
        sa.Column("providerNameSnapshot", sa.String(128)),
        sa.Column("endpointSnapshot", sa.String(500)),
        sa.Column("modelSnapshot", sa.String(128)),
        sa.Column("encryptedApiKeySnapshot", sa.Text()),
        sa.Column("timeoutSeconds", sa.Integer(), nullable=False, server_default=sa.text("90")),
        sa.Column("cancellationRequestedAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
        sa.Column("createdAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint('(\"providerConfigId\" IS NULL AND \"providerNameSnapshot\" IS NULL AND \"endpointSnapshot\" IS NULL AND \"modelSnapshot\" IS NULL AND \"encryptedApiKeySnapshot\" IS NULL) OR (\"providerConfigId\" IS NOT NULL AND \"providerNameSnapshot\" IS NOT NULL AND \"endpointSnapshot\" IS NOT NULL AND \"modelSnapshot\" IS NOT NULL AND \"encryptedApiKeySnapshot\" IS NOT NULL)', name="provider_snapshot_pair"),
        sa.CheckConstraint('\"timeoutSeconds\" = 90', name="timeout_seconds_fixed"),
        sa.ForeignKeyConstraint(["taskId"], ["durable_tasks.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["conversationId"], ["agent_conversations.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["userMessageId"], ["agent_messages.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["requestedByUserId"], ["users.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["providerConfigId"], ["ai_provider_configs.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("agent_execution_configs_taskId_key", "agent_execution_configs", ["taskId"], unique=True)
    op.create_index("agent_execution_configs_userMessageId_key", "agent_execution_configs", ["userMessageId"], unique=True)
    op.execute('''
    CREATE FUNCTION agent_execution_configs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD."taskId" IS DISTINCT FROM NEW."taskId" OR OLD."conversationId" IS DISTINCT FROM NEW."conversationId" OR OLD."userMessageId" IS DISTINCT FROM NEW."userMessageId" OR OLD."requestedByUserId" IS DISTINCT FROM NEW."requestedByUserId" OR OLD."providerConfigId" IS DISTINCT FROM NEW."providerConfigId" OR OLD."providerNameSnapshot" IS DISTINCT FROM NEW."providerNameSnapshot" OR OLD."endpointSnapshot" IS DISTINCT FROM NEW."endpointSnapshot" OR OLD."modelSnapshot" IS DISTINCT FROM NEW."modelSnapshot" OR OLD."encryptedApiKeySnapshot" IS DISTINCT FROM NEW."encryptedApiKeySnapshot" OR OLD."timeoutSeconds" IS DISTINCT FROM NEW."timeoutSeconds" THEN RAISE EXCEPTION 'agent execution config is immutable'; END IF;
      IF OLD."cancellationRequestedAt" IS NOT NULL AND NEW."cancellationRequestedAt" IS DISTINCT FROM OLD."cancellationRequestedAt" THEN RAISE EXCEPTION 'cancellation request is immutable'; END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER agent_execution_configs_immutable_guard BEFORE UPDATE ON agent_execution_configs FOR EACH ROW EXECUTE FUNCTION agent_execution_configs_immutable();
    ''')
    # ADR 0028 keeps all direct references RESTRICT while requiring the secret
    # snapshot to leave with its owning conversation retention boundary.
    op.execute('''
    CREATE FUNCTION delete_agent_execution_configs_with_conversation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      DELETE FROM agent_execution_configs WHERE "conversationId" = OLD.id;
      RETURN OLD;
    END; $$;
    CREATE TRIGGER agent_execution_configs_conversation_retention
      BEFORE DELETE ON agent_conversations FOR EACH ROW
      EXECUTE FUNCTION delete_agent_execution_configs_with_conversation();
    ''')


def downgrade() -> None:
    raise NotImplementedError("ADR 0028 execution facts are not destructively downgradable")
