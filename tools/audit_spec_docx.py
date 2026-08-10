#!/usr/bin/env python3
"""Structural and coverage audit for the generated specification DOCX."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_spec_docx.py <document.docx>", file=sys.stderr)
        return 2

    docx_path = Path(sys.argv[1]).resolve()
    with zipfile.ZipFile(docx_path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    text = "".join(node.text or "" for node in root.iter(f"{W}t"))
    tables = list(root.iter(f"{W}tbl"))
    cells = list(root.iter(f"{W}tc"))

    missing_tbl_w = sum(
        table.find(f"{W}tblPr/{W}tblW") is None for table in tables
    )
    missing_tbl_grid = sum(table.find(f"{W}tblGrid") is None for table in tables)
    missing_tc_w = sum(cell.find(f"{W}tcPr/{W}tcW") is None for cell in cells)

    required_files = [
        "01-login.html",
        "02-dashboard.html",
        "03-admin-dashboard.html",
        "04-user-management.html",
        "05-role-permissions.html",
        "06-project-import.html",
        "07-api-key-management.html",
        "08-system-config.html",
        "09-audit-logs.html",
        "10-mailbox-settings.html",
        "11-mail-sync-results.html",
    ]
    required_terms = [
        "风险详情弹框",
        "周报详情弹框",
        "API Key 管理",
        "系统配置",
        "审计日志",
        "风险管理员个人邮箱配置",
        "邮箱同步结果",
        "最小字号",
        "320px",
        "Excel",
        "角色权限",
    ]

    missing_files = [item for item in required_files if item not in text]
    missing_terms = [item for item in required_terms if item not in text]
    replacement_chars = text.count("\ufffd")

    result = {
        "document": str(docx_path),
        "character_count": len(text),
        "tables": len(tables),
        "cells": len(cells),
        "missing_table_widths": missing_tbl_w,
        "missing_table_grids": missing_tbl_grid,
        "missing_cell_widths": missing_tc_w,
        "required_page_files": len(required_files),
        "missing_page_files": missing_files,
        "required_terms": len(required_terms),
        "missing_terms": missing_terms,
        "replacement_characters": replacement_chars,
    }
    result["passed"] = not any(
        [
            missing_tbl_w,
            missing_tbl_grid,
            missing_tc_w,
            missing_files,
            missing_terms,
            replacement_chars,
        ]
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
