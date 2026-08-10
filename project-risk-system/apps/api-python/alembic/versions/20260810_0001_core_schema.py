"""Create the mature Prisma-equivalent core schema baseline."""

from pathlib import Path
from typing import Final

from alembic import op

revision: Final = "20260810_0001"
down_revision: Final = None
branch_labels: Final = None
depends_on: Final = None

_SQL = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    """Apply structure only; Seed and T006 audit enforcement are intentionally absent."""

    op.get_bind().exec_driver_sql(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    """Baseline downgrade is deliberately unsupported to avoid destructive data loss."""

    raise NotImplementedError(
        "核心 schema baseline 不提供破坏性 downgrade; 请恢复备份或重建隔离数据库"
    )
