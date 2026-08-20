"""Private filesystem storage for uploaded workbooks."""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from uuid import UUID

from risk_platform.imports.parser import MAX_WORKBOOK_BYTES, WorkbookError


class WorkbookStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @staticmethod
    def validate(file_name: str, content: bytes) -> str:
        safe = Path(file_name).name
        if safe != file_name or not re.fullmatch(r"[^/\\]{1,255}\.xlsx", safe, re.IGNORECASE):
            raise WorkbookError("请选择 .xlsx 格式的项目清单 Excel 文件")
        if not content or len(content) > MAX_WORKBOOK_BYTES:
            raise WorkbookError("Excel 文件大小不能超过 20MB")
        if not content.startswith(b"PK") or not zipfile.is_zipfile(BytesIO(content)):
            raise WorkbookError("上传文件不是有效的 Excel 工作簿")
        return safe

    async def save(self, batch_id: UUID, file_name: str, content: bytes) -> tuple[str, str]:
        safe = self.validate(file_name, content)
        del safe
        digest = hashlib.sha256(content).hexdigest()
        key = f"{batch_id}/source.xlsx"
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise WorkbookError("文件存储路径无效")
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(target, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
        except BaseException:
            with suppress(FileNotFoundError):
                target.unlink()
            raise
        return key, digest

    def read(self, key: str) -> bytes:
        target = (self.root / key).resolve()
        if self.root not in target.parents or target.name != "source.xlsx":
            raise WorkbookError("文件存储路径无效")
        return target.read_bytes()

    def remove_batch(self, batch_id: UUID) -> None:
        target = (self.root / str(batch_id)).resolve()
        if self.root not in target.parents:
            raise WorkbookError("文件存储路径无效")
        for child in target.iterdir() if target.exists() else ():
            child.unlink()
        if target.exists():
            target.rmdir()


__all__ = ["WorkbookStorage"]
