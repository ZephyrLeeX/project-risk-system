"""Agent runtime scope rules, cache revision, and SYSTEM_ADMIN grant.

Three things happen here:

1. ``agent_scope_rules`` — admin-managed runtime layer-1 scope rules
   (ALLOW/BLOCK, EXACT/PHRASE match, soft-deleted, created disabled).
2. ``agent_scope_rule_revision`` — single-row BIGINT revision bumped inside
   every rule mutation transaction; rule caches compare it to reload.
3. Idempotent grant of the new ``agent.scope.manage`` permission to the
   SYSTEM_ADMIN role so already-deployed databases pick it up on upgrade
   (the deploy pipeline runs migrations, not the seed).
"""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260820_0019"
down_revision: Final = "20260820_0018"
branch_labels: Final = None
depends_on: Final = None

_GRANT_PERMISSION_SQL = """
INSERT INTO permissions (id, code, name, module, description, "createdAt")
SELECT gen_random_uuid(), 'agent.scope.manage', '管理 Agent 范围规则', 'AGENT',
       '新增、修改、启用、禁用和测试 Agent 第一层 scope 规则', CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'agent.scope.manage')
"""

_GRANT_ROLE_SQL = """
INSERT INTO role_permissions ("roleId", "permissionId", "grantedAt")
SELECT r.id, p.id, CURRENT_TIMESTAMP
FROM roles r JOIN permissions p ON p.code = 'agent.scope.manage'
WHERE r.code = 'SYSTEM_ADMIN'
ON CONFLICT DO NOTHING
"""


def upgrade() -> None:
    decision = postgresql.ENUM(
        "ALLOW",
        "BLOCK",
        name="AgentScopeRuleDecision",
        create_type=False,
    )
    match_type = postgresql.ENUM("EXACT", "PHRASE", name="AgentScopeRuleMatchType", create_type=False)
    for enum in (decision, match_type):
        enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "agent_scope_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("decision", decision, nullable=False),
        sa.Column("matchType", match_type, nullable=False),
        sa.Column("pattern", sa.String(200), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("description", sa.String(500)),
        sa.Column("createdBy", postgresql.UUID(as_uuid=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("deletedAt", postgresql.TIMESTAMP(timezone=True, precision=3)),
        sa.Column(
            "createdAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updatedAt",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
        ),
        sa.CheckConstraint('"priority" BETWEEN 0 AND 1000', name="agent_scope_rules_priority_range"),
        sa.CheckConstraint('"version" >= 1', name="agent_scope_rules_version_positive"),
        sa.CheckConstraint("btrim(\"pattern\") <> ''", name="agent_scope_rules_pattern_nonempty"),
        sa.ForeignKeyConstraint(
            ["createdBy"], ["users.id"], name="agent_scope_rules_createdBy_fkey", ondelete="SET NULL"
        ),
    )
    op.execute(
        'CREATE UNIQUE INDEX "agent_scope_rules_name_active_key" ON agent_scope_rules ("name") '
        'WHERE "deletedAt" IS NULL'
    )
    op.create_index("agent_scope_rules_enabled_idx", "agent_scope_rules", ["enabled"])
    op.create_index("agent_scope_rules_deletedAt_idx", "agent_scope_rules", ["deletedAt"])
    op.create_table(
        "agent_scope_rule_revision",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint('"id" = 1', name="agent_scope_rule_revision_single_row"),
        sa.CheckConstraint('"revision" >= 0', name="agent_scope_rule_revision_nonnegative"),
    )
    op.execute('INSERT INTO agent_scope_rule_revision ("id", "revision") VALUES (1, 0)')
    # Grant the managing permission to SYSTEM_ADMIN on already-deployed
    # databases (idempotent; the seed covers fresh installs).
    op.execute(_GRANT_PERMISSION_SQL)
    op.execute(_GRANT_ROLE_SQL)


def downgrade() -> None:
    raise NotImplementedError("Agent scope rules are not destructively downgradable")
