"""Add mobile identity mapping and authentication method to sessions."""

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: Final = "20260818_0015"
down_revision: Final = "20260818_0014"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    auth_method = postgresql.ENUM("PASSWORD", "WECHAT", name="AuthMethod", create_type=False)
    auth_method.create(op.get_bind(), checkfirst=True)
    op.add_column("users", sa.Column("mobile", sa.String(32), nullable=True))
    op.create_index(
        "users_mobile_key", "users", ["mobile"], unique=True,
        postgresql_where=sa.text('"mobile" IS NOT NULL'),
    )
    op.add_column(
        "sessions",
        sa.Column("authMethod", auth_method, nullable=False, server_default="PASSWORD"),
    )


def downgrade() -> None:
    raise NotImplementedError("WeChat SSO migration is not destructively downgradable")
