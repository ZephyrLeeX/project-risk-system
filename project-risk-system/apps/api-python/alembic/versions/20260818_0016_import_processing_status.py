"""Represent the asynchronous import parsing lifecycle explicitly."""

from typing import Final

from alembic import op

revision: Final = "20260818_0016"
down_revision: Final = "20260818_0015"
branch_labels: Final = None
depends_on: Final = None


def upgrade() -> None:
    op.execute(
        'ALTER TYPE "ImportBatchStatus" ADD VALUE IF NOT EXISTS '
        "'PROCESSING' BEFORE 'PREVIEWED'"
    )
    op.execute('ALTER TABLE "import_batches" ALTER COLUMN "status" SET DEFAULT \'PROCESSING\'')


def downgrade() -> None:
    raise NotImplementedError("Import processing status is not destructively downgradable")
