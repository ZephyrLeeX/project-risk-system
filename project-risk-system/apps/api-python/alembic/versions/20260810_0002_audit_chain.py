"""Replace snapshots with typed metadata and enforce the audit hash chain."""

from pathlib import Path
from typing import Final

from alembic import op

revision: Final = "20260810_0002"
down_revision: Final = "20260810_0001"
branch_labels: Final = None
depends_on: Final = None

_SQL = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        _SQL.read_text(encoding="utf-8").split("-- downgrade", maxsplit=1)[0]
    )


def downgrade() -> None:
    raise NotImplementedError(
        "metadata-only audit migration 不提供破坏性 downgrade, 避免恢复已禁止的 snapshot schema"
    )
