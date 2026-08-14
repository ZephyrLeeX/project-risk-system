"""Restore-time file reconciliation (ADR 0031 §2, §8).

After the file archive is extracted into the isolated restore target and the DB
is restored, reconcile verifies the two are consistent *without* relying on
"copy happened to align":

* every durable file the restored DB references must be present (missing ->
  fail-closed);
* any extracted file the DB does not reference is an orphan and is discarded
  (defensive; handles stray/partial files).

The DB references import source workbooks via ``import_batches.storageKey``
(relative to the import storage dir, ``<storage_root>/excel``). Other durable
areas (e.g. ``mail/``) hold no DB-referenced files; any file there is an orphan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from risk_backup.errors import BackupError

IMPORT_SUBDIR = "excel"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    referenced_count: int
    present_count: int
    orphans_removed: list[str]
    missing: list[str]


def reconcile_files(
    storage_root: Path,
    referenced_storage_keys: list[str],
    *,
    import_subdir: str = IMPORT_SUBDIR,
) -> ReconcileResult:
    """Discard orphan files and fail-closed on missing referenced files.

    ``storage_root`` is the extracted file-archive root (the restored durable
    volume). ``referenced_storage_keys`` are ``import_batches.storageKey`` values
    relative to ``<storage_root>/<import_subdir>``.
    """

    import_root = storage_root / import_subdir
    referenced = {(import_root / key).resolve() for key in referenced_storage_keys}
    present = {p.resolve() for p in storage_root.rglob("*") if p.is_file()}

    missing = sorted(str(p.relative_to(storage_root)) for p in (referenced - present))
    orphans = referenced.symmetric_difference(present) & present  # present but not referenced
    orphan_paths = sorted(orphans)

    for orphan in orphan_paths:
        try:
            orphan.unlink()
        except OSError as exc:
            raise BackupError("RESTORE_ORPHAN_CLEANUP_FAILED", cause=exc) from exc
    # Remove now-empty orphan directories (best-effort, not fail-closed).
    _prune_empty_dirs(storage_root)

    if missing:
        raise BackupError("RESTORE_MISSING_REFERENCED_FILE")

    return ReconcileResult(
        referenced_count=len(referenced),
        present_count=len(present),
        orphans_removed=[str(p.relative_to(storage_root)) for p in orphan_paths],
        missing=missing,
    )


def _prune_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue


__all__ = ["IMPORT_SUBDIR", "ReconcileResult", "reconcile_files"]
