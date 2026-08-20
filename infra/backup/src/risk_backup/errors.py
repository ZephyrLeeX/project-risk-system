"""Stable, key-material-free error type for backup/restore failures.

Errors carry only a machine code (ADR 0031 §10 — metadata-only logging; never
include keys, plaintext, file contents or dump contents in messages).
"""

from __future__ import annotations


class BackupError(Exception):
    """Fail-closed backup/restore error identified by a stable code.

    The message is the code itself; no key material, plaintext or payload
    content is ever attached.
    """

    def __init__(self, code: str, *, cause: BaseException | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.__cause__ = cause


class CleanupWarning(BackupError):
    """Plaintext temp cleanup failed on an otherwise-complete backup.

    The encrypted artifact may still be ``USABLE`` (ADR 0031 §9), but a residual
    plaintext file is a SEVERE security alert that operators must investigate.
    """
