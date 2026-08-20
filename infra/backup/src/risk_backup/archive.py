"""Durable file-storage archiver (ADR 0031 §1, §3).

Tars the durable application file storage (the ``project-risk-storage`` volume,
container path ``/app/storage``) into a reproducible archive, excluding
temporary / scratch subdirectories (ADR 0031 §1 — temp/scratch, parse products,
orphan temp are never backed up). The archive is deterministic (sorted entries,
zeroed mtime/uid/gid) so its sha256 is stable and the manifest binding verifiable.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

from risk_backup.errors import BackupError

# Default scratch/temp exclude globs matched against each path component. The
# durable volume holds ``excel/`` (import sources) and ``mail/``; parse and temp
# products live in the system tempdir (outside the volume) but these globs also
# defend against any stray scratch written under the volume.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "__pycache__",
    "*.tmp",
    "*.tmp.*",
    ".tmp_*",
    "*.part",
    "*.lock",
    "*.log",
)


@dataclass(frozen=True, slots=True)
class FileArchiveInfo:
    root_path: str
    entry_count: int
    size_bytes: int
    sha256: str
    excludes: list[str]


def _is_excluded(rel_path: Path, excludes: list[str]) -> bool:
    from fnmatch import fnmatch

    for part in rel_path.parts:
        for pattern in excludes:
            if fnmatch(part, pattern):
                return True
    return False


def _iter_durable_files(root: Path, excludes: list[str]) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if _is_excluded(rel, excludes):
            continue
        files.append(path)
    return files


def _normalized_info(path: Path, root: Path, content: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=str(path.relative_to(root)))
    info.size = len(content)
    info.mtime = 0
    info.mode = 0o600
    info.type = tarfile.REGTYPE
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def archive_files(
    root: Path,
    out_tar: Path,
    *,
    excludes: list[str] | None = None,
    extra_exclude_dirs: list[Path] | None = None,
) -> FileArchiveInfo:
    """Archive ``root`` into ``out_tar`` (deterministic tar, gzip-free).

    ``extra_exclude_dirs`` lets the caller exclude the backup output directory
    when it happens to live under the storage volume (defensive; the runbook keeps
    them separate).
    """

    if not root.is_dir():
        raise BackupError("FILES_ROOT_MISSING")
    used_excludes = list(excludes) if excludes is not None else list(DEFAULT_EXCLUDES)
    files = _iter_durable_files(root, used_excludes)
    if extra_exclude_dirs:
        normalized_excluded = {p.resolve() for p in extra_exclude_dirs}
        files = [f for f in files if f.resolve() not in normalized_excluded]
    sha = hashlib.sha256()
    size = 0
    tmp_path = out_tar.with_suffix(out_tar.suffix + ".tmp")
    try:
        with tmp_path.open("wb") as raw, tarfile.open(fileobj=raw, mode="w") as tar:
            for path in files:
                content = path.read_bytes()
                info = _normalized_info(path, root, content)
                tar.addfile(info, _BytesReader(content))
                sha.update(content)
                size += len(content)
        os.replace(tmp_path, out_tar)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    archive_size = out_tar.stat().st_size
    archive_sha = _file_sha256(out_tar)
    return FileArchiveInfo(
        root_path=str(root),
        entry_count=len(files),
        size_bytes=archive_size,
        sha256=archive_sha,
        excludes=used_excludes,
    )


def extract_files(tar_path: Path, dest_root: Path) -> None:
    """Extract a files archive into ``dest_root`` (the isolated restore target)."""

    if not tar_path.is_file():
        raise BackupError("FILES_ARCHIVE_MISSING")
    dest_root.mkdir(parents=True, exist_ok=True)
    dest_root_resolved = dest_root.resolve()
    try:
        with tarfile.open(tar_path, mode="r") as tar:
            for member in tar.getmembers():
                # Defensive path traversal guard: every member must resolve under
                # the restore root (restore target is isolated/empty by default).
                target = (dest_root / member.name).resolve()
                if dest_root_resolved != target and dest_root_resolved not in target.parents:
                    raise BackupError("FILES_ARCHIVE_PATH_ESCAPE")
            tar.extractall(dest_root, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise BackupError("FILES_ARCHIVE_CORRUPT", cause=exc) from exc


def _file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


class _BytesReader:
    """Minimal file-like object backing ``tarfile.addfile`` from in-memory bytes."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        result = self._data[self._pos:] if size < 0 else self._data[self._pos : self._pos + size]
        self._pos += len(result)
        return result


def file_sha256(path: Path) -> str:
    """Public sha256 of a file (used by tests and the pgdump component)."""

    return _file_sha256(path)


__all__ = [
    "DEFAULT_EXCLUDES",
    "FileArchiveInfo",
    "archive_files",
    "extract_files",
    "file_sha256",
]
