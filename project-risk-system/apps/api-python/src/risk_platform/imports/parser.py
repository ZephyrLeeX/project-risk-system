"""Bounded, dependency-free parser for the approved project workbook format."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Final
from xml.etree import ElementTree

MAIN_SHEET: Final = "数据回款"
SUPPLEMENTAL_SHEET: Final = "涵谷回款"
LEGAL_SHEET: Final = "发函-诉讼清单"
MAX_WORKBOOK_BYTES: Final = 20 * 1024 * 1024
_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


@dataclass(frozen=True, slots=True)
class MonthlyCollection:
    month: int
    amount: str | None
    attribute: str | None
    fill_color: str | None


@dataclass(slots=True)
class ParsedRow:
    row_number: int
    import_key: str
    action: str
    status: str
    external_code: str | None
    project_name: str | None
    department_name: str | None
    delivery_owner_name: str | None
    annual_plan_amount: str | None
    actual_collected_amount: str | None
    remaining_amount: str | None
    monthly_collections: list[MonthlyCollection]
    month_attributes: dict[str, str | None]
    collection_risk_level: str
    collection_progress: str | None
    source_snapshot: dict[str, object]
    warnings: list[str]
    errors: list[str]
    matched_project_id: str | None = None


@dataclass(slots=True)
class ParsedSupplementalRow:
    row_number: int
    source_key: str
    status: str
    match_status: str
    external_code: str | None
    project_name: str | None
    contract_receivable_amount: str | None
    procurement_contract_amount: str | None
    cumulative_collected_amount: str | None
    remaining_uncollected_amount: str | None
    actual_collected_this_year: str | None
    actual_collected_net_this_year: str | None
    annual_collection_plan: str | None
    collection_risk_level: str
    monthly_collections: list[MonthlyCollection]
    month_attributes: dict[str, str | None]
    after_year_amount: str | None
    source_snapshot: dict[str, object]
    warnings: list[str]
    errors: list[str]
    matched_import_key: str | None = None
    matched_project_id: str | None = None


@dataclass(slots=True)
class ParsedLegalRow:
    row_number: int
    source_key: str
    status: str
    match_status: str
    external_code: str | None
    project_name: str | None
    department_name: str | None
    delivery_owner_name: str | None
    annual_plan_amount: str | None
    collection_risk_level: str
    legal_progress: str | None
    monthly_collections: list[MonthlyCollection]
    month_attributes: dict[str, str | None]
    source_snapshot: dict[str, object]
    warnings: list[str]
    errors: list[str]
    matched_import_key: str | None = None
    matched_project_id: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedWorkbook:
    sheet_name: str
    sheet_names: list[str]
    ignored_sheets: list[str]
    month_attributes: dict[str, str | None]
    rows: list[ParsedRow]
    supplemental_rows: list[ParsedSupplementalRow]
    legal_rows: list[ParsedLegalRow]


class WorkbookError(ValueError):
    """Raised for an invalid or unsupported workbook."""


class _Sheet:
    def __init__(
        self,
        name: str,
        rows: dict[int, dict[int, str | None]],
        fills: dict[tuple[int, int], str | None],
        hidden: set[int],
    ) -> None:
        self.name = name
        self.rows = rows
        self.fills = fills
        self.hidden = hidden

    def cell(self, row: int, column: int) -> str | None:
        return (self.rows.get(row) or {}).get(column)

    def row_count(self) -> int:
        return max(self.rows, default=0)


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        raise WorkbookError("工作簿包含无效单元格地址")
    result = 0
    for letter in letters.group(0):
        result = result * 26 + ord(letter) - 64
    return result


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _key(prefix: str, *values: str | None) -> str:
    raw = prefix + "|" + "|".join(_normalized(value) for value in values)
    return hashlib.sha256(raw.encode()).hexdigest()


def _risk(value: str | None) -> str:
    return {
        "高": "HIGH",
        "高风险": "HIGH",
        "中": "MEDIUM",
        "中风险": "MEDIUM",
        "低": "LOW",
        "低风险": "LOW",
    }.get(value or "", "UNKNOWN")


def _amount(
    sheet: _Sheet,
    row: int,
    column: int,
    field: str,
    errors: list[str],
    *,
    nonnegative: bool = False,
) -> str | None:
    raw = _text(sheet.cell(row, column))
    if raw is None or raw in {"-", "—"}:
        return None
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        errors.append(f"{field}不是有效金额")
        return None
    if not value.is_finite() or (nonnegative and value < 0):
        errors.append(f"{field}{'不能为负数' if nonnegative else '不是有效金额'}")
        return None
    return format(value, "f")


def _status(errors: list[str], warnings: list[str]) -> str:
    return "ERROR" if errors else "WARNING" if warnings else "READY"


class ProjectListParser:
    """Parse only approved sheets and preserve untrusted source values as snapshots."""

    def parse(self, content: bytes) -> ParsedWorkbook:
        if not content or len(content) > MAX_WORKBOOK_BYTES:
            raise WorkbookError("Excel 文件大小必须在 1 字节至 20MB 之间")
        sheets = self._load(content)
        if MAIN_SHEET not in sheets:
            raise WorkbookError(f"未找到“{MAIN_SHEET}”工作表")
        main = sheets[MAIN_SHEET]
        self._headers(
            main,
            3,
            {
                1: "交付部门",
                2: "交付负责人",
                3: "项目编码",
                4: "项目名称",
                20: "回款风险",
                21: "回款进展",
            },
        )
        attrs = {str(index): _text(main.cell(2, 7 + index)) for index in range(1, 13)}
        rows = self._main_rows(main, attrs)
        supplemental = self._supplemental(sheets.get(SUPPLEMENTAL_SHEET))
        legal = self._legal(sheets.get(LEGAL_SHEET))
        names = [_normalized(row.project_name) for row in rows]
        keys = [row.import_key for row in rows]
        seen: set[str] = set()
        for row, name, key in zip(rows, names, keys, strict=True):
            if names.count(name) > 1:
                row.warnings.append("同一文件存在重名项目\uff0c确认导入前请核对")
            if keys.count(key) > 1:
                duplicate = key in seen
                if duplicate:
                    row.import_key = _key(key, "DUPLICATE_ROW", str(row.row_number))
                    row.action = "UPDATE" if row.external_code else "CREATE"
                row.warnings.append(
                    (
                        "同一文件项目编码重复\uff0c确认后将合并更新同一项目"
                        if row.external_code
                        else "组合匹配字段完全重复\uff0c已按源文件行号区分"
                    )
                    if duplicate
                    else (
                        "同一文件项目编码重复\uff0c后续重复行将合并到本项目"
                        if row.external_code
                        else "组合匹配字段完全重复\uff0c已按源文件行号区分"
                    )
                )
            seen.add(key)
            row.status = _status(row.errors, row.warnings)
        return ParsedWorkbook(
            sheet_name=MAIN_SHEET,
            sheet_names=list(sheets),
            ignored_sheets=[
                name for name in sheets if name not in {MAIN_SHEET, SUPPLEMENTAL_SHEET, LEGAL_SHEET}
            ],
            month_attributes=attrs,
            rows=rows,
            supplemental_rows=supplemental,
            legal_rows=legal,
        )

    def _main_rows(self, sheet: _Sheet, attrs: dict[str, str | None]) -> list[ParsedRow]:
        result: list[ParsedRow] = []
        for row_number in range(4, sheet.row_count() + 1):
            department, owner, code, name = (sheet.cell(row_number, index) for index in range(1, 5))
            if not any((department, owner, code, name)):
                continue
            errors: list[str] = []
            warnings: list[str] = []
            if not department:
                errors.append("交付部门不能为空")
            if not owner:
                errors.append("交付负责人不能为空")
            if not name:
                errors.append("项目名称不能为空")
            if not code:
                warnings.append("项目编码为空\uff0c将使用项目名称、部门和负责人组合匹配")
            plan = _amount(sheet, row_number, 5, "年度计划", errors)
            actual = _amount(sheet, row_number, 6, "实际已回款", errors)
            remaining = _amount(sheet, row_number, 7, "剩余待回款", errors)
            monthly = [
                MonthlyCollection(
                    month,
                    _amount(sheet, row_number, 7 + month, f"{month}月金额", errors),
                    attrs[str(month)],
                    sheet.fills.get((row_number, 7 + month)),
                )
                for month in range(1, 13)
            ]
            risk_text = _text(sheet.cell(row_number, 20))
            risk = _risk(risk_text)
            if risk_text and risk == "UNKNOWN":
                warnings.append(f"无法识别回款风险“{risk_text}”")
            import_key = _key("CODE", code) if code else _key("COMPOSITE", name, department, owner)
            result.append(
                ParsedRow(
                    row_number,
                    import_key,
                    "CREATE",
                    _status(errors, warnings),
                    code,
                    name,
                    department,
                    owner,
                    plan,
                    actual,
                    remaining,
                    monthly,
                    attrs,
                    risk,
                    _text(sheet.cell(row_number, 21)),
                    {
                        "values": [sheet.cell(row_number, i) for i in range(1, 22)],
                        "monthFillColors": [
                            {"month": item.month, "color": item.fill_color} for item in monthly
                        ],
                    },
                    warnings,
                    errors,
                )
            )
        return result

    def _supplemental(self, sheet: _Sheet | None) -> list[ParsedSupplementalRow]:
        if sheet is None:
            return []
        self._headers(
            sheet,
            2,
            {
                1: "项目编码",
                2: "项目名称",
                3: "合同应收金额",
                4: "采购合同总额",
                5: "累计已收款额",
                6: "剩余未回款",
                7: "26年实际回款",
                8: "26年实际回款净额",
                9: "26年回款计划",
                10: "回款风险",
                23: "26年以后",
            },
        )
        attrs = {str(index): _text(sheet.cell(1, 10 + index)) for index in range(1, 13)}
        result: list[ParsedSupplementalRow] = []
        for row_number in range(3, sheet.row_count() + 1):
            code, name = sheet.cell(row_number, 1), sheet.cell(row_number, 2)
            if not code and not name:
                continue
            errors: list[str] = []
            warnings: list[str] = []
            if not name:
                errors.append("项目名称不能为空")
            if not code:
                warnings.append("项目编码为空\uff0c将按项目名称尝试匹配主项目")
            amounts = [
                _amount(sheet, row_number, column, field, errors, nonnegative=True)
                for column, field in (
                    (3, "合同应收金额"),
                    (4, "采购合同总额"),
                    (5, "累计已收款额"),
                    (6, "剩余未回款"),
                    (7, "26年实际回款"),
                    (8, "26年实际回款净额"),
                    (9, "26年回款计划"),
                )
            ]
            monthly = [
                MonthlyCollection(
                    month,
                    _amount(
                        sheet, row_number, 10 + month, f"{month}月金额", errors, nonnegative=True
                    ),
                    attrs[str(month)],
                    sheet.fills.get((row_number, 10 + month)),
                )
                for month in range(1, 13)
            ]
            risk_text = _text(sheet.cell(row_number, 10))
            risk = _risk(risk_text)
            if risk_text and risk == "UNKNOWN":
                warnings.append(f"无法识别回款风险“{risk_text}”")
            snapshot = {
                "values": [sheet.cell(row_number, i) for i in range(1, 24)],
                "hiddenColumns": [{"column": i, "hidden": True} for i in sorted(sheet.hidden)],
                "monthFillColors": [
                    {"month": item.month, "color": item.fill_color} for item in monthly
                ],
            }
            result.append(
                ParsedSupplementalRow(
                    row_number=row_number,
                    source_key=_key("CODE" if code else "NAME", code or name),
                    status=_status(errors, warnings),
                    match_status="UNMATCHED",
                    external_code=code,
                    project_name=name,
                    contract_receivable_amount=amounts[0],
                    procurement_contract_amount=amounts[1],
                    cumulative_collected_amount=amounts[2],
                    remaining_uncollected_amount=amounts[3],
                    actual_collected_this_year=amounts[4],
                    actual_collected_net_this_year=amounts[5],
                    annual_collection_plan=amounts[6],
                    collection_risk_level=risk,
                    monthly_collections=monthly,
                    month_attributes=attrs,
                    after_year_amount=_amount(
                        sheet, row_number, 23, "26年以后", errors, nonnegative=True
                    ),
                    source_snapshot=snapshot,
                    warnings=warnings,
                    errors=errors,
                )
            )
        return result

    def _legal(self, sheet: _Sheet | None) -> list[ParsedLegalRow]:
        if sheet is None:
            return []
        self._headers(
            sheet,
            3,
            {
                1: "交付部门",
                2: "交付负责人",
                3: "项目编码",
                4: "项目名称",
                5: "2026年计划滚测小计",
                18: "回款风险",
                19: "回款进展",
            },
        )
        attrs = {str(index): _text(sheet.cell(2, 5 + index)) for index in range(1, 13)}
        result: list[ParsedLegalRow] = []
        for row_number in range(4, sheet.row_count() + 1):
            department, owner, code, name = (sheet.cell(row_number, index) for index in range(1, 5))
            if not any((department, owner, code, name)):
                continue
            errors: list[str] = []
            warnings: list[str] = []
            if not department:
                errors.append("交付部门不能为空")
            if not owner:
                errors.append("交付负责人不能为空")
            if not name:
                errors.append("项目名称不能为空")
            if not code:
                warnings.append("项目编码为空\uff0c将按项目名称尝试匹配主项目")
            plan = _amount(sheet, row_number, 5, "年度计划", errors, nonnegative=True)
            monthly = [
                MonthlyCollection(
                    month,
                    _amount(
                        sheet, row_number, 5 + month, f"{month}月金额", errors, nonnegative=True
                    ),
                    attrs[str(month)],
                    sheet.fills.get((row_number, 5 + month)),
                )
                for month in range(1, 13)
            ]
            risk_text = _text(sheet.cell(row_number, 18))
            risk = _risk(risk_text)
            if risk_text and risk == "UNKNOWN":
                warnings.append(f"无法识别回款风险“{risk_text}”")
            progress = _text(sheet.cell(row_number, 19))
            if not progress:
                warnings.append("法务进展为空")
            result.append(
                ParsedLegalRow(
                    row_number,
                    _key("CODE" if code else "NAME", code or name),
                    _status(errors, warnings),
                    "UNMATCHED",
                    code,
                    name,
                    department,
                    owner,
                    plan,
                    risk,
                    progress,
                    monthly,
                    attrs,
                    {
                        "values": [sheet.cell(row_number, i) for i in range(1, 20)],
                        "hiddenColumns": [
                            {"column": i, "hidden": True} for i in sorted(sheet.hidden)
                        ],
                        "monthFillColors": [
                            {"month": item.month, "color": item.fill_color} for item in monthly
                        ],
                    },
                    warnings,
                    errors,
                )
            )
        return result

    @staticmethod
    def _headers(sheet: _Sheet, row: int, expected: dict[int, str]) -> None:
        for column, value in expected.items():
            if _text(sheet.cell(row, column)) != value:
                raise WorkbookError(f"“{sheet.name}”第{row}行第{column}列表头应为“{value}”")

    def _load(self, content: bytes) -> dict[str, _Sheet]:
        try:
            archive = zipfile.ZipFile(BytesIO(content))
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names:
                raise WorkbookError("文件不是有效的 Excel 工作簿")
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root.findall("m:si", _NS)]
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rels = {
                item.attrib["Id"]: item.attrib["Target"]
                for item in relationships.findall("pr:Relationship", _NS)
            }
            styles = self._styles(archive.read("xl/styles.xml")) if "xl/styles.xml" in names else {}
            result: dict[str, _Sheet] = {}
            for sheet in workbook.findall("m:sheets/m:sheet", _NS):
                target = rels.get(sheet.attrib[f"{{{_NS['r']}}}id"])
                if not target:
                    raise WorkbookError("工作簿工作表关系无效")
                path = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
                result[sheet.attrib["name"]] = self._sheet(
                    sheet.attrib["name"], archive.read(path), shared, styles
                )
            return result
        except (KeyError, ElementTree.ParseError, zipfile.BadZipFile) as exc:
            raise WorkbookError("Excel 文件损坏或格式不受支持") from exc

    @staticmethod
    def _styles(content: bytes) -> dict[int, str | None]:
        root = ElementTree.fromstring(content)
        fills = root.findall("m:fills/m:fill", _NS)
        colors: list[str | None] = []
        for fill in fills:
            color = fill.find("m:patternFill/m:fgColor", _NS)
            colors.append(color.attrib.get("rgb") if color is not None else None)
        result: dict[int, str | None] = {}
        for index, xf in enumerate(root.findall("m:cellXfs/m:xf", _NS)):
            fill_id = int(xf.attrib.get("fillId", "0"))
            result[index] = colors[fill_id] if fill_id < len(colors) else None
        return result

    @staticmethod
    def _sheet(
        name: str, content: bytes, shared: list[str], styles: dict[int, str | None]
    ) -> _Sheet:
        root = ElementTree.fromstring(content)
        rows: dict[int, dict[int, str | None]] = {}
        fills: dict[tuple[int, int], str | None] = {}
        hidden: set[int] = set()
        for column_node in root.findall("m:cols/m:col", _NS):
            if column_node.attrib.get("hidden") == "1":
                hidden.update(
                    range(
                        int(column_node.attrib["min"]),
                        int(column_node.attrib["max"]) + 1,
                    )
                )
        for row in root.findall("m:sheetData/m:row", _NS):
            row_number = int(row.attrib["r"])
            values: dict[int, str | None] = {}
            for cell in row.findall("m:c", _NS):
                column_number = _column_number(cell.attrib["r"])
                value = cell.find("m:v", _NS)
                kind = cell.attrib.get("t")
                if kind == "inlineStr":
                    inline = cell.find("m:is", _NS)
                    value_text = "".join(inline.itertext()) if inline is not None else None
                elif value is None:
                    value_text = None
                elif kind == "s":
                    value_text = shared[int(value.text or "0")]
                elif kind == "b":
                    value_text = "是" if value.text == "1" else "否"
                else:
                    value_text = value.text
                values[column_number] = value_text
                fills[(row_number, column_number)] = styles.get(int(cell.attrib.get("s", "0")))
            rows[row_number] = values
        return _Sheet(name, rows, fills, hidden)


__all__ = ["MAX_WORKBOOK_BYTES", "ParsedWorkbook", "ProjectListParser", "WorkbookError"]
