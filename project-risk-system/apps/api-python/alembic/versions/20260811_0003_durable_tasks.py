"""Add the ADR 0018 durable task and transactional outbox contract."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260811_0003"
down_revision: Final = "20260810_0002"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.create_table(
        "durable_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "IMPORT_PREVIEW",
                "MAILBOX_SYNC",
                "MAIL_MESSAGE_RETRY",
                "ATTACHMENT_PARSE",
                "MAIL_AI_REVIEW_PUBLISH",
                "RETENTION_CLEANUP",
                name="DurableTaskKind",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "RUNNING",
                "RETRY_WAIT",
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
                name="DurableTaskStatus",
            ),
            server_default=sa.text("'QUEUED'"),
            nullable=False,
        ),
        sa.Column("idempotencyKey", sa.String(length=255), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attemptCount", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("maxAttempts", sa.Integer(), nullable=False),
        sa.Column("nextRetryAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True),
        sa.Column("leaseToken", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("leaseOwner", sa.String(length=255), nullable=True),
        sa.Column("heartbeatAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True),
        sa.Column(
            "leaseExpiresAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True
        ),
        sa.Column(
            "dispatchGeneration", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("failureCode", sa.String(length=128), nullable=True),
        sa.Column("failureSummary", sa.Text(), nullable=True),
        sa.Column("startedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True),
        sa.Column("completedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(precision=3, timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("updatedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=False),
        sa.CheckConstraint(
            '"attemptCount" >= 0 AND "attemptCount" <= "maxAttempts" AND "maxAttempts" > 0',
            name="attempt_count_bounds",
        ),
        sa.CheckConstraint(
            '"dispatchGeneration" >= 0',
            name="dispatch_generation_nonnegative",
        ),
        sa.CheckConstraint(
            'btrim("idempotencyKey") <> \'\'',
            name="idempotency_key_nonempty",
        ),
        sa.CheckConstraint(
            '"leaseExpiresAt" IS NULL OR "leaseExpiresAt" > "heartbeatAt"',
            name="lease_expiry_after_heartbeat",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND \"leaseToken\" IS NOT NULL "
            "AND \"leaseOwner\" IS NOT NULL AND \"heartbeatAt\" IS NOT NULL "
            "AND \"leaseExpiresAt\" IS NOT NULL) OR "
            "(status <> 'RUNNING' AND \"leaseToken\" IS NULL "
            "AND \"leaseOwner\" IS NULL AND \"heartbeatAt\" IS NULL "
            "AND \"leaseExpiresAt\" IS NULL)",
            name="lease_state",
        ),
        sa.CheckConstraint(
            "(status = 'RETRY_WAIT' AND \"nextRetryAt\" IS NOT NULL) OR "
            "(status <> 'RETRY_WAIT' AND \"nextRetryAt\" IS NULL)",
            name="retry_schedule_state",
        ),
        sa.CheckConstraint(
            "(status IN ('SUCCEEDED', 'FAILED', 'CANCELLED') AND \"completedAt\" IS NOT NULL) OR "
            "(status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED') AND \"completedAt\" IS NULL)",
            name="completion_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="payload_object"
        ),
        sa.PrimaryKeyConstraint("id", name="durable_tasks_pkey"),
    )
    op.create_index(
        "durable_tasks_kind_idempotencyKey_key",
        "durable_tasks",
        ["kind", "idempotencyKey"],
        unique=True,
    )
    op.create_index(
        "durable_tasks_status_leaseExpiresAt_idx",
        "durable_tasks",
        ["status", "leaseExpiresAt"],
        unique=False,
    )
    op.create_index(
        "durable_tasks_status_nextRetryAt_idx",
        "durable_tasks",
        ["status", "nextRetryAt"],
        unique=False,
    )

    op.create_table(
        "task_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taskId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dispatchGeneration", sa.Integer(), nullable=False),
        sa.Column("publishedAt", postgresql.TIMESTAMP(precision=3, timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(precision=3, timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            '"dispatchGeneration" > 0',
            name="dispatch_generation_positive",
        ),
        sa.ForeignKeyConstraint(
            ["taskId"],
            ["durable_tasks.id"],
            name="task_outbox_taskId_fkey",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="task_outbox_pkey"),
    )
    op.create_index(
        "task_outbox_publishedAt_createdAt_idx",
        "task_outbox",
        ["publishedAt", "createdAt"],
        unique=False,
    )
    op.create_index(
        "task_outbox_taskId_dispatchGeneration_key",
        "task_outbox",
        ["taskId", "dispatchGeneration"],
        unique=True,
    )

    op.add_column(
        "import_batches",
        sa.Column("taskId", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_unique_constraint("import_batches_taskId_key", "import_batches", ["taskId"])
    op.create_foreign_key(
        "import_batches_taskId_fkey",
        "import_batches",
        "durable_tasks",
        ["taskId"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    op.add_column(
        "mail_sync_batches",
        sa.Column("taskId", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_unique_constraint(
        "mail_sync_batches_taskId_key", "mail_sync_batches", ["taskId"]
    )
    op.create_foreign_key(
        "mail_sync_batches_taskId_fkey",
        "mail_sync_batches",
        "durable_tasks",
        ["taskId"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "durable task schema 不提供破坏性 downgrade; 请恢复备份或重建隔离数据库"
    )
