from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from risk_platform.imports.parser import ProjectListParser, WorkbookError


def _xlsx(main_name: str = "数据回款") -> bytes:
    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{main_name}" sheetId="1" r:id="rId1"/>'
        '<sheet name="汇总" sheetId="2" r:id="rId2"/></sheets></workbook>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>'
    )
    values = {
        1: "交付部门",
        2: "交付负责人",
        3: "项目编码",
        4: "项目名称",
        20: "回款风险",
        21: "回款进展",
    }
    headers = "".join(
        f'<c r="{chr(64 + col)}3" t="inlineStr"><is><t>{text}</t></is></c>'
        for col, text in values.items()
    )
    cells = "".join(
        f'<c r="{chr(64 + col)}4" t="inlineStr"><is><t>{text}</t></is></c>'
        for col, text in {1: "一部", 2: "张三", 3: "P-1", 4: "项目", 20: "高", 21: "跟进"}.items()
    )
    cells += (
        '<c r="E4"><v>100</v></c><c r="F4"><v>40</v></c>'
        '<c r="G4"><v>60</v></c><c r="H4"><v>0</v></c>'
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="3">{headers}</row><row r="4">{cells}</row>'
        "</sheetData></worksheet>"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr(
            "xl/worksheets/sheet2.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>',
        )
    return output.getvalue()


def test_parser_preserves_zero_and_validates_project_rows() -> None:
    parsed = ProjectListParser().parse(_xlsx())
    assert parsed.sheet_names == ["数据回款", "汇总"]
    assert parsed.ignored_sheets == ["汇总"]
    assert len(parsed.rows) == 1
    assert parsed.rows[0].collection_risk_level == "HIGH"
    assert parsed.rows[0].monthly_collections[0].amount == "0"
    assert parsed.rows[0].annual_plan_amount == "100"


def test_parser_rejects_missing_main_sheet() -> None:
    with pytest.raises(WorkbookError, match="未找到"):
        ProjectListParser().parse(_xlsx("其他"))


def test_parser_rejects_oversized_workbook() -> None:
    with pytest.raises(WorkbookError, match="20MB"):
        ProjectListParser().parse(b"x" * (20 * 1024 * 1024 + 1))
