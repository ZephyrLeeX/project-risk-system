"""Plaintext temp lifecycle (ADR 0031 §4).

Plaintext (pgdump, tar, pre-encryption payload) may exist only for the duration
of one backup job, in a controlled temp directory separate from the artifact
output directory. Cleanup is mandatory on success AND failure; a cleanup failure
does not invalidate an already-encrypted artifact but raises ``CleanupWarning``
(SEVERE) so operators scrub the temp dir before trusting the backup.
"""

from __future__ import annotations

import os
from pathlib import Path

from risk_backup.errors import CleanupWarning


def cleanup_plaintext(paths: list[Path]) -> None:
    """Overwrite-then-unlink every plaintext temp file/dir.

    Raises ``CleanupWarning`` (SEVERE) if any path could not be removed; the
    encrypted artifact may still be ``USABLE`` (ADR 0031 §9).
    """

    failures: list[str] = []
    # Files first (overwrite), then directories bottom-up.
    file_paths = [p for p in paths if p.is_file()]
    for path in file_paths:
        try:
            _overwrite(path)
            path.unlink()
        except OSError:
            failures.append(str(path))
    for path in sorted((p for p in paths if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            for child in path.rglob("*"):
                if child.is_file():
                    _overwrite(child)
                    child.unlink()
            path.rmdir()
        except OSError:
            failures.append(str(path))
    if failures:
        raise CleanupWarning("PLAINTEXT_CLEANUP_FAILED")


def _overwrite(path: Path) -> None:
    """Best-effort single-pass overwrite before unlink."""

    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= 0:
        return
    with open(path, "rb+") as handle:
        handle.write(b"\x00" * size)
        handle.flush()
        os.fsync(handle.fileno())


__all__ = ["cleanup_plaintext"]
