"""Add frozen retention facts and auditable deletion-protection holds."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260812_0006"
down_revision: Final = "20260811_0005"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.add_column(
        "import_batches",
        sa.Column("sourceExpiresAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
    )
    op.add_column(
        "import_batches",
        sa.Column("rollbackProtectedUntil", postgresql.TIMESTAMP(timezone=True, precision=3)),
    )
    op.add_column("import_batches", sa.Column("retentionConfigVersion", sa.String(length=32)))
    op.add_column("agent_conversations", sa.Column("retentionConfigVersion", sa.String(length=32)))

    # Old releases predate the retention object.  The ADR-approved defaults are used only
    # for this additive backfill; no source, business fact, or audit record is rewritten.
    op.execute(
        """
        DO $$
        DECLARE
            import_days integer := 365;
            conversation_days integer := 90;
            rollback_days integer := 30;
            config_version text := 'ADR0027_DEFAULT';
            configured jsonb;
        BEGIN
            SELECT version, snapshot->'retention'
            INTO config_version, configured
            FROM system_config_releases
            ORDER BY "publishedAt" DESC, id DESC
            LIMIT 1;

            IF config_version IS NULL THEN
                config_version := 'ADR0027_DEFAULT';
            END IF;
            IF configured IS NOT NULL
                AND jsonb_typeof(configured->'importSourceRetentionDays') = 'number'
                AND (configured->>'importSourceRetentionDays')::integer BETWEEN 30 AND 730
            THEN
                import_days := (configured->>'importSourceRetentionDays')::integer;
            END IF;
            IF configured IS NOT NULL
                AND jsonb_typeof(configured->'agentConversationRetentionDays') = 'number'
                AND (configured->>'agentConversationRetentionDays')::integer BETWEEN 30 AND 365
            THEN
                conversation_days := (configured->>'agentConversationRetentionDays')::integer;
            END IF;
            IF configured IS NOT NULL
                AND jsonb_typeof(configured->'importRollbackProtectionDays') = 'number'
                AND (configured->>'importRollbackProtectionDays')::integer BETWEEN 7 AND 90
            THEN
                rollback_days := (configured->>'importRollbackProtectionDays')::integer;
            END IF;

            UPDATE import_batches
            SET "sourceExpiresAt" = "createdAt" + make_interval(days => import_days),
                "rollbackProtectedUntil" = CASE
                    WHEN status = 'IMPORTED' AND "confirmedAt" IS NOT NULL
                    THEN "confirmedAt" + make_interval(days => rollback_days)
                    ELSE NULL
                END,
                "retentionConfigVersion" = config_version;

            UPDATE agent_conversations
            SET "expiresAt" = "createdAt" + make_interval(days => conversation_days),
                "retentionConfigVersion" = config_version;
        END $$;
        """
    )
    op.alter_column("import_batches", "sourceExpiresAt", nullable=False)
    op.alter_column("import_batches", "retentionConfigVersion", nullable=False)
    op.alter_column("agent_conversations", "retentionConfigVersion", nullable=False)
    op.create_index("import_batches_sourceExpiresAt_idx", "import_batches", ["sourceExpiresAt"])
    op.create_index(
        "import_batches_rollbackProtectedUntil_idx", "import_batches", ["rollbackProtectedUntil"]
    )

    op.create_table(
        "retention_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "resourceType",
            sa.Enum(
                "IMPORT_BATCH", "AGENT_CONVERSATION", "BACKUP_COPY", name="RetentionResourceType"
            ),
            nullable=False,
        ),
        sa.Column("resourceId", sa.String(length=128), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "LEGAL", "INVESTIGATION", "INCIDENT", "RESTORE_DRILL", name="RetentionHoldReason"
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "RELEASED", "EXPIRED", name="RetentionHoldStatus"),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("createdById", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("createdTraceId", sa.String(length=64), nullable=False),
        sa.Column("expiresAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.Column("releasedAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.Column("releasedById", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("releasedTraceId", sa.String(length=64), nullable=True),
        sa.Column("expiredAt", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.Column("expiredById", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expiredTraceId", sa.String(length=64), nullable=True),
        sa.CheckConstraint("btrim(\"resourceId\") <> ''", name="resource_id_nonempty"),
        sa.CheckConstraint("btrim(\"createdTraceId\") <> ''", name="created_trace_nonempty"),
        sa.CheckConstraint(
            '"expiresAt" IS NULL OR "expiresAt" > "createdAt"', name="expiry_after_creation"
        ),
        sa.CheckConstraint(
            '("status" = \'ACTIVE\' AND "releasedAt" IS NULL AND "releasedById" IS NULL '
            'AND "releasedTraceId" IS NULL AND "expiredAt" IS NULL AND "expiredById" IS NULL '
            'AND "expiredTraceId" IS NULL) OR '
            '("status" = \'RELEASED\' AND "releasedAt" IS NOT NULL AND "releasedById" IS NOT NULL '
            'AND "releasedTraceId" IS NOT NULL AND "expiredAt" IS NULL AND "expiredById" IS NULL '
            'AND "expiredTraceId" IS NULL) OR '
            '("status" = \'EXPIRED\' AND "releasedAt" IS NULL AND "releasedById" IS NULL '
            'AND "releasedTraceId" IS NULL AND "expiredAt" IS NOT NULL '
            'AND "expiredTraceId" IS NOT NULL)',
            name="terminal_facts_match_status",
        ),
        sa.ForeignKeyConstraint(
            ["createdById"], ["users.id"], ondelete="RESTRICT", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["releasedById"], ["users.id"], ondelete="RESTRICT", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["expiredById"], ["users.id"], ondelete="RESTRICT", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "retention_holds_resource_status_idx",
        "retention_holds",
        ["resourceType", "resourceId", "status"],
    )
    op.create_index("retention_holds_expiresAt_idx", "retention_holds", ["expiresAt"])
    op.create_index(
        "retention_holds_active_resource_key",
        "retention_holds",
        ["resourceType", "resourceId"],
        unique=True,
        postgresql_where=sa.text("\"status\" = 'ACTIVE'"),
    )
    op.execute(
        """
        CREATE FUNCTION retention_holds_enforce_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'retention hold deletion is forbidden';
            END IF;

            IF OLD."createdAt" IS DISTINCT FROM NEW."createdAt"
                OR OLD."createdById" IS DISTINCT FROM NEW."createdById"
                OR OLD."createdTraceId" IS DISTINCT FROM NEW."createdTraceId"
                OR OLD."resourceType" IS DISTINCT FROM NEW."resourceType"
                OR OLD."resourceId" IS DISTINCT FROM NEW."resourceId"
                OR OLD.reason IS DISTINCT FROM NEW.reason
                OR OLD."expiresAt" IS DISTINCT FROM NEW."expiresAt"
            THEN
                RAISE EXCEPTION 'retention hold creation facts are immutable';
            END IF;

            IF OLD.status <> 'ACTIVE' THEN
                RAISE EXCEPTION 'terminal retention hold cannot be updated';
            END IF;

            IF NEW.status = 'RELEASED'
                AND NEW."releasedAt" IS NOT NULL
                AND NEW."releasedById" IS NOT NULL
                AND NEW."releasedTraceId" IS NOT NULL
                AND NEW."expiredAt" IS NULL
                AND NEW."expiredById" IS NULL
                AND NEW."expiredTraceId" IS NULL
            THEN
                RETURN NEW;
            END IF;

            IF NEW.status = 'EXPIRED'
                AND NEW."releasedAt" IS NULL
                AND NEW."releasedById" IS NULL
                AND NEW."releasedTraceId" IS NULL
                AND NEW."expiredAt" IS NOT NULL
                AND NEW."expiredTraceId" IS NOT NULL
            THEN
                RETURN NEW;
            END IF;

            RAISE EXCEPTION 'invalid retention hold lifecycle transition';
        END;
        $$;

        CREATE TRIGGER retention_holds_lifecycle_guard
        BEFORE UPDATE OR DELETE ON retention_holds
        FOR EACH ROW EXECUTE FUNCTION retention_holds_enforce_lifecycle();
        """
    )


def downgrade() -> None:
    raise NotImplementedError("T042 migration is not destructively downgradable")
