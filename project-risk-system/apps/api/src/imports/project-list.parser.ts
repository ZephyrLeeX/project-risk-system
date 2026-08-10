import { createHash } from "node:crypto";

import type {
  ImportRowAction,
  ImportRowStatus,
  LegalMatterMatchStatus,
  ProjectRiskLevel,
  SupplementalMatchStatus,
} from "@prisma/client";
import ExcelJS = require("exceljs");

const MAIN_SHEET_NAME = "数据回款";
const HEADER_ROW = 3;
const FIRST_DATA_ROW = 4;
const MONTH_START_COLUMN = 8;
const MONTH_END_COLUMN = 19;
const SUPPLEMENTAL_SHEET_NAME = "涵谷回款";
const SUPPLEMENTAL_HEADER_ROW = 2;
const SUPPLEMENTAL_FIRST_DATA_ROW = 3;
const SUPPLEMENTAL_MONTH_START_COLUMN = 11;
const SUPPLEMENTAL_MONTH_END_COLUMN = 22;
const LEGAL_SHEET_NAME = "发函-诉讼清单";
const LEGAL_HEADER_ROW = 3;
const LEGAL_FIRST_DATA_ROW = 4;
const LEGAL_MONTH_START_COLUMN = 6;
const LEGAL_MONTH_END_COLUMN = 17;

export interface ParsedMonthlyCollection {
  month: number;
  amount: string | null;
  attribute: string | null;
  fillColor: string | null;
}

export interface ParsedProjectImportRow {
  rowNumber: number;
  importKey: string;
  action: ImportRowAction;
  status: ImportRowStatus;
  externalCode: string | null;
  projectName: string | null;
  departmentName: string | null;
  deliveryOwnerName: string | null;
  annualPlanAmount: string | null;
  actualCollectedAmount: string | null;
  remainingAmount: string | null;
  monthlyCollections: ParsedMonthlyCollection[];
  monthAttributes: Record<string, string | null>;
  collectionRiskLevel: ProjectRiskLevel;
  collectionProgress: string | null;
  sourceSnapshot: Record<string, unknown>;
  warnings: string[];
  errors: string[];
  matchedProjectId?: string;
}

export interface ParsedSupplementalCollectionRow {
  rowNumber: number;
  sourceKey: string;
  status: ImportRowStatus;
  matchStatus: SupplementalMatchStatus;
  matchedImportKey?: string;
  matchedProjectId?: string;
  externalCode: string | null;
  projectName: string | null;
  contractReceivableAmount: string | null;
  procurementContractAmount: string | null;
  cumulativeCollectedAmount: string | null;
  remainingUncollectedAmount: string | null;
  actualCollectedThisYear: string | null;
  actualCollectedNetThisYear: string | null;
  annualCollectionPlan: string | null;
  collectionRiskLevel: ProjectRiskLevel;
  monthlyCollections: ParsedMonthlyCollection[];
  monthAttributes: Record<string, string | null>;
  afterYearAmount: string | null;
  sourceSnapshot: Record<string, unknown>;
  warnings: string[];
  errors: string[];
}

export interface ParsedLegalMatterRow {
  rowNumber: number;
  sourceKey: string;
  status: ImportRowStatus;
  matchStatus: LegalMatterMatchStatus;
  matchedImportKey?: string;
  matchedProjectId?: string;
  externalCode: string | null;
  projectName: string | null;
  departmentName: string | null;
  deliveryOwnerName: string | null;
  annualPlanAmount: string | null;
  collectionRiskLevel: ProjectRiskLevel;
  legalProgress: string | null;
  monthlyCollections: ParsedMonthlyCollection[];
  monthAttributes: Record<string, string | null>;
  sourceSnapshot: Record<string, unknown>;
  warnings: string[];
  errors: string[];
}

export interface ParsedProjectWorkbook {
  sheetName: string;
  sheetNames: string[];
  ignoredSheets: string[];
  monthAttributes: Record<string, string | null>;
  rows: ParsedProjectImportRow[];
  supplementalCollections: ParsedSupplementalCollectionRow[];
  legalMatters: ParsedLegalMatterRow[];
}

function cellText(cell: ExcelJS.Cell): string | null {
  const text = cell.text.trim();
  return text.length > 0 ? text : null;
}

function normalized(value: string | null): string {
  return (value ?? "")
    .normalize("NFKC")
    .trim()
    .replace(/\s+/g, " ")
    .toLocaleLowerCase("zh-CN");
}

function hashImportKey(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function baseImportKey(
  externalCode: string | null,
  projectName: string | null,
  departmentName: string | null,
  deliveryOwnerName: string | null,
): string {
  if (externalCode) {
    return `CODE|${normalized(externalCode)}`;
  }
  return [
    "COMPOSITE",
    normalized(projectName),
    normalized(departmentName),
    normalized(deliveryOwnerName),
  ].join("|");
}

function cellNumericText(
  cell: ExcelJS.Cell,
  fieldName: string,
  errors: string[],
): string | null {
  const raw = cell.value;
  if (raw === null || raw === undefined || cell.text.trim() === "") {
    return null;
  }

  const formulaResult =
    typeof raw === "object" && raw && "result" in raw
      ? raw.result
      : raw;
  if (typeof formulaResult === "number") {
    return Number.isFinite(formulaResult) ? String(formulaResult) : null;
  }

  const text = String(formulaResult ?? "")
    .replace(/,/g, "")
    .trim();
  if (text === "" || text === "-" || text === "—") {
    return null;
  }
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) {
    errors.push(`${fieldName}不是有效金额`);
    return null;
  }
  return text;
}

function fillColor(cell: ExcelJS.Cell): string | null {
  const fill = cell.fill;
  if (!fill || fill.type !== "pattern") {
    return null;
  }
  return fill.fgColor?.argb ?? fill.bgColor?.argb ?? null;
}

function riskLevel(value: string | null): ProjectRiskLevel {
  if (value === "高" || value === "高风险") return "HIGH";
  if (value === "中" || value === "中风险") return "MEDIUM";
  if (value === "低" || value === "低风险") return "LOW";
  return "UNKNOWN";
}

function decimalSumMatches(
  plan: string,
  actual: string,
  remaining: string,
): boolean {
  const scale = 100;
  const toCents = (value: string): number =>
    Math.round(Number(value) * scale);
  return toCents(plan) === toCents(actual) + toCents(remaining);
}

export class ProjectListParser {
  async parse(buffer: Buffer): Promise<ParsedProjectWorkbook> {
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.load(
      buffer as unknown as Parameters<typeof workbook.xlsx.load>[0],
    );

    const sheetNames = workbook.worksheets.map(({ name }) => name);
    const sheet = workbook.getWorksheet(MAIN_SHEET_NAME);
    if (!sheet) {
      throw new Error(`未找到“${MAIN_SHEET_NAME}”工作表`);
    }

    const expectedHeaders = [
      [1, "交付部门"],
      [2, "交付负责人"],
      [3, "项目编码"],
      [4, "项目名称"],
      [20, "回款风险"],
      [21, "回款进展"],
    ] as const;
    for (const [column, expected] of expectedHeaders) {
      if (cellText(sheet.getRow(HEADER_ROW).getCell(column)) !== expected) {
        throw new Error(
          `“${MAIN_SHEET_NAME}”第${HEADER_ROW}行第${column}列表头应为“${expected}”`,
        );
      }
    }

    const monthAttributes: Record<string, string | null> = {};
    for (
      let column = MONTH_START_COLUMN;
      column <= MONTH_END_COLUMN;
      column += 1
    ) {
      monthAttributes[String(column - MONTH_START_COLUMN + 1)] = cellText(
        sheet.getRow(2).getCell(column),
      );
    }

    const provisional: Array<
      ParsedProjectImportRow & { baseKey: string }
    > = [];
    for (let rowNumber = FIRST_DATA_ROW; rowNumber <= sheet.rowCount; rowNumber += 1) {
      const row = sheet.getRow(rowNumber);
      const departmentName = cellText(row.getCell(1));
      const deliveryOwnerName = cellText(row.getCell(2));
      const externalCode = cellText(row.getCell(3));
      const projectName = cellText(row.getCell(4));

      if (
        !departmentName &&
        !deliveryOwnerName &&
        !externalCode &&
        !projectName
      ) {
        continue;
      }

      const errors: string[] = [];
      const warnings: string[] = [];
      if (!departmentName) errors.push("交付部门不能为空");
      if (!deliveryOwnerName) errors.push("交付负责人不能为空");
      if (!projectName) errors.push("项目名称不能为空");
      if (!externalCode) warnings.push("项目编码为空，将使用项目名称、部门和负责人组合匹配");

      const annualPlanAmount = cellNumericText(
        row.getCell(5),
        "年度计划",
        errors,
      );
      const actualCollectedAmount = cellNumericText(
        row.getCell(6),
        "实际已回款",
        errors,
      );
      const remainingAmount = cellNumericText(
        row.getCell(7),
        "剩余待回款",
        errors,
      );
      if (
        annualPlanAmount !== null &&
        actualCollectedAmount !== null &&
        remainingAmount !== null &&
        !decimalSumMatches(
          annualPlanAmount,
          actualCollectedAmount,
          remainingAmount,
        )
      ) {
        warnings.push("年度计划不等于实际已回款与剩余待回款之和");
      }

      const monthlyCollections: ParsedMonthlyCollection[] = [];
      for (
        let column = MONTH_START_COLUMN;
        column <= MONTH_END_COLUMN;
        column += 1
      ) {
        const month = column - MONTH_START_COLUMN + 1;
        monthlyCollections.push({
          month,
          amount: cellNumericText(
            row.getCell(column),
            `${month}月金额`,
            errors,
          ),
          attribute: monthAttributes[String(month)] ?? null,
          fillColor: fillColor(row.getCell(column)),
        });
      }

      const riskText = cellText(row.getCell(20));
      const collectionRiskLevel = riskLevel(riskText);
      if (riskText && collectionRiskLevel === "UNKNOWN") {
        warnings.push(`无法识别回款风险“${riskText}”`);
      }

      const keySource = baseImportKey(
        externalCode,
        projectName,
        departmentName,
        deliveryOwnerName,
      );
      provisional.push({
        rowNumber,
        baseKey: keySource,
        importKey: hashImportKey(keySource),
        action: "CREATE",
        status: errors.length > 0 ? "ERROR" : warnings.length > 0 ? "WARNING" : "READY",
        externalCode,
        projectName,
        departmentName,
        deliveryOwnerName,
        annualPlanAmount,
        actualCollectedAmount,
        remainingAmount,
        monthlyCollections,
        monthAttributes,
        collectionRiskLevel,
        collectionProgress: cellText(row.getCell(21)),
        sourceSnapshot: {
          values: Array.from({ length: 21 }, (_, index) =>
            cellText(row.getCell(index + 1)),
          ),
          monthFillColors: monthlyCollections.map(
            ({ month, fillColor: color }) => ({ month, color }),
          ),
        },
        warnings,
        errors,
      });
    }

    const nameCounts = new Map<string, number>();
    const keyCounts = new Map<string, number>();
    for (const row of provisional) {
      const name = normalized(row.projectName);
      nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);
      keyCounts.set(row.baseKey, (keyCounts.get(row.baseKey) ?? 0) + 1);
    }
    const seenBaseKeys = new Set<string>();
    for (const row of provisional) {
      if ((nameCounts.get(normalized(row.projectName)) ?? 0) > 1) {
        row.warnings.push("同一文件存在重名项目，确认导入前请核对");
      }
      if ((keyCounts.get(row.baseKey) ?? 0) > 1) {
        if (seenBaseKeys.has(row.baseKey)) {
          row.importKey = hashImportKey(
            `${row.baseKey}|DUPLICATE_ROW|${row.rowNumber}`,
          );
          if (row.externalCode) {
            row.action = "UPDATE";
          }
          row.warnings.push(
            row.externalCode
              ? "同一文件项目编码重复，确认后将合并更新同一项目"
              : "组合匹配字段完全重复，已按源文件行号区分",
          );
        } else {
          row.warnings.push(
            row.externalCode
              ? "同一文件项目编码重复，后续重复行将合并到本项目"
              : "组合匹配字段完全重复，已按源文件行号区分",
          );
        }
      }
      seenBaseKeys.add(row.baseKey);
      row.status =
        row.errors.length > 0
          ? "ERROR"
          : row.warnings.length > 0
            ? "WARNING"
            : "READY";
      delete (row as Partial<typeof row>).baseKey;
    }

    const supplementalCollections = this.parseSupplementalCollections(
      workbook.getWorksheet(SUPPLEMENTAL_SHEET_NAME),
    );
    const legalMatters = this.parseLegalMatters(
      workbook.getWorksheet(LEGAL_SHEET_NAME),
    );

    return {
      sheetName: MAIN_SHEET_NAME,
      sheetNames,
      ignoredSheets: sheetNames.filter(
        (name) =>
          name !== MAIN_SHEET_NAME &&
          name !== SUPPLEMENTAL_SHEET_NAME &&
          name !== LEGAL_SHEET_NAME,
      ),
      monthAttributes,
      rows: provisional,
      supplementalCollections,
      legalMatters,
    };
  }

  private parseSupplementalCollections(
    sheet: ExcelJS.Worksheet | undefined,
  ): ParsedSupplementalCollectionRow[] {
    if (!sheet) {
      return [];
    }

    const expectedHeaders = [
      [1, "项目编码"],
      [2, "项目名称"],
      [3, "合同应收金额"],
      [4, "采购合同总额"],
      [5, "累计已收款额"],
      [6, "剩余未回款"],
      [7, "26年实际回款"],
      [8, "26年实际回款净额"],
      [9, "26年回款计划"],
      [10, "回款风险"],
      [23, "26年以后"],
    ] as const;
    for (const [column, expected] of expectedHeaders) {
      if (
        cellText(sheet.getRow(SUPPLEMENTAL_HEADER_ROW).getCell(column)) !==
        expected
      ) {
        throw new Error(
          `“${SUPPLEMENTAL_SHEET_NAME}”第${SUPPLEMENTAL_HEADER_ROW}行第${column}列表头应为“${expected}”`,
        );
      }
    }

    const monthAttributes: Record<string, string | null> = {};
    for (
      let column = SUPPLEMENTAL_MONTH_START_COLUMN;
      column <= SUPPLEMENTAL_MONTH_END_COLUMN;
      column += 1
    ) {
      monthAttributes[
        String(column - SUPPLEMENTAL_MONTH_START_COLUMN + 1)
      ] = cellText(sheet.getRow(1).getCell(column));
    }

    const rows: ParsedSupplementalCollectionRow[] = [];
    for (
      let rowNumber = SUPPLEMENTAL_FIRST_DATA_ROW;
      rowNumber <= sheet.rowCount;
      rowNumber += 1
    ) {
      const row = sheet.getRow(rowNumber);
      const externalCode = cellText(row.getCell(1));
      const projectName = cellText(row.getCell(2));
      if (!externalCode && !projectName) {
        continue;
      }

      const errors: string[] = [];
      const warnings: string[] = [];
      if (!projectName) {
        errors.push("项目名称不能为空");
      }
      if (!externalCode) {
        warnings.push("项目编码为空，将按项目名称尝试匹配主项目");
      }

      const amount = (column: number, fieldName: string): string | null => {
        const value = cellNumericText(row.getCell(column), fieldName, errors);
        if (value !== null && Number(value) < 0) {
          errors.push(`${fieldName}不能为负数`);
        }
        return value;
      };
      const monthlyCollections: ParsedMonthlyCollection[] = [];
      for (
        let column = SUPPLEMENTAL_MONTH_START_COLUMN;
        column <= SUPPLEMENTAL_MONTH_END_COLUMN;
        column += 1
      ) {
        const month =
          column - SUPPLEMENTAL_MONTH_START_COLUMN + 1;
        monthlyCollections.push({
          month,
          amount: amount(column, `${month}月金额`),
          attribute: monthAttributes[String(month)] ?? null,
          fillColor: fillColor(row.getCell(column)),
        });
      }

      const riskText = cellText(row.getCell(10));
      const collectionRiskLevel = riskLevel(riskText);
      if (riskText && collectionRiskLevel === "UNKNOWN") {
        warnings.push(`无法识别回款风险“${riskText}”`);
      }
      const contractReceivableAmount = amount(3, "合同应收金额");
      const procurementContractAmount = amount(4, "采购合同总额");
      const cumulativeCollectedAmount = amount(5, "累计已收款额");
      const remainingUncollectedAmount = amount(6, "剩余未回款");
      const actualCollectedThisYear = amount(7, "26年实际回款");
      const actualCollectedNetThisYear = amount(
        8,
        "26年实际回款净额",
      );
      const annualCollectionPlan = amount(9, "26年回款计划");
      const afterYearAmount = amount(23, "26年以后");
      const keySource = externalCode
        ? `CODE|${normalized(externalCode)}`
        : `NAME|${normalized(projectName)}`;
      rows.push({
        rowNumber,
        sourceKey: hashImportKey(keySource),
        status:
          errors.length > 0
            ? "ERROR"
            : warnings.length > 0
              ? "WARNING"
              : "READY",
        matchStatus: "UNMATCHED",
        externalCode,
        projectName,
        contractReceivableAmount,
        procurementContractAmount,
        cumulativeCollectedAmount,
        remainingUncollectedAmount,
        actualCollectedThisYear,
        actualCollectedNetThisYear,
        annualCollectionPlan,
        collectionRiskLevel,
        monthlyCollections,
        monthAttributes,
        afterYearAmount,
        sourceSnapshot: {
          values: Array.from({ length: 23 }, (_, index) =>
            cellText(row.getCell(index + 1)),
          ),
          hiddenColumns: Array.from({ length: 23 }, (_, index) => ({
            column: index + 1,
            hidden: Boolean(sheet.getColumn(index + 1).hidden),
          })).filter(({ hidden }) => hidden),
          monthFillColors: monthlyCollections.map(
            ({ month, fillColor: color }) => ({ month, color }),
          ),
        },
        warnings,
        errors,
      });
    }
    return rows;
  }

  private parseLegalMatters(
    sheet: ExcelJS.Worksheet | undefined,
  ): ParsedLegalMatterRow[] {
    if (!sheet) {
      return [];
    }

    const expectedHeaders = [
      [1, "交付部门"],
      [2, "交付负责人"],
      [3, "项目编码"],
      [4, "项目名称"],
      [5, "2026年计划滚测小计"],
      [18, "回款风险"],
      [19, "回款进展"],
    ] as const;
    for (const [column, expected] of expectedHeaders) {
      if (cellText(sheet.getRow(LEGAL_HEADER_ROW).getCell(column)) !== expected) {
        throw new Error(
          `“${LEGAL_SHEET_NAME}”第${LEGAL_HEADER_ROW}行第${column}列表头应为“${expected}”`,
        );
      }
    }

    const monthAttributes: Record<string, string | null> = {};
    for (
      let column = LEGAL_MONTH_START_COLUMN;
      column <= LEGAL_MONTH_END_COLUMN;
      column += 1
    ) {
      monthAttributes[String(column - LEGAL_MONTH_START_COLUMN + 1)] =
        cellText(sheet.getRow(2).getCell(column));
    }

    const rows: ParsedLegalMatterRow[] = [];
    for (
      let rowNumber = LEGAL_FIRST_DATA_ROW;
      rowNumber <= sheet.rowCount;
      rowNumber += 1
    ) {
      const row = sheet.getRow(rowNumber);
      const departmentName = cellText(row.getCell(1));
      const deliveryOwnerName = cellText(row.getCell(2));
      const externalCode = cellText(row.getCell(3));
      const projectName = cellText(row.getCell(4));
      if (
        !departmentName &&
        !deliveryOwnerName &&
        !externalCode &&
        !projectName
      ) {
        continue;
      }

      const errors: string[] = [];
      const warnings: string[] = [];
      if (!departmentName) errors.push("交付部门不能为空");
      if (!deliveryOwnerName) errors.push("交付负责人不能为空");
      if (!projectName) errors.push("项目名称不能为空");
      if (!externalCode) {
        warnings.push("项目编码为空，将按项目名称尝试匹配主项目");
      }
      const amount = (column: number, fieldName: string): string | null => {
        const value = cellNumericText(row.getCell(column), fieldName, errors);
        if (value !== null && Number(value) < 0) {
          errors.push(`${fieldName}不能为负数`);
        }
        return value;
      };
      const annualPlanAmount = amount(5, "年度计划");
      const monthlyCollections: ParsedMonthlyCollection[] = [];
      for (
        let column = LEGAL_MONTH_START_COLUMN;
        column <= LEGAL_MONTH_END_COLUMN;
        column += 1
      ) {
        const month = column - LEGAL_MONTH_START_COLUMN + 1;
        monthlyCollections.push({
          month,
          amount: amount(column, `${month}月金额`),
          attribute: monthAttributes[String(month)] ?? null,
          fillColor: fillColor(row.getCell(column)),
        });
      }
      const riskText = cellText(row.getCell(18));
      const collectionRiskLevel = riskLevel(riskText);
      if (riskText && collectionRiskLevel === "UNKNOWN") {
        warnings.push(`无法识别回款风险“${riskText}”`);
      }
      const legalProgress = cellText(row.getCell(19));
      if (!legalProgress) {
        warnings.push("法务进展为空");
      }
      const keySource = externalCode
        ? `CODE|${normalized(externalCode)}`
        : `NAME|${normalized(projectName)}`;
      rows.push({
        rowNumber,
        sourceKey: hashImportKey(keySource),
        status:
          errors.length > 0
            ? "ERROR"
            : warnings.length > 0
              ? "WARNING"
              : "READY",
        matchStatus: "UNMATCHED",
        externalCode,
        projectName,
        departmentName,
        deliveryOwnerName,
        annualPlanAmount,
        collectionRiskLevel,
        legalProgress,
        monthlyCollections,
        monthAttributes,
        sourceSnapshot: {
          values: Array.from({ length: 19 }, (_, index) =>
            cellText(row.getCell(index + 1)),
          ),
          hiddenColumns: Array.from({ length: 19 }, (_, index) => ({
            column: index + 1,
            hidden: Boolean(sheet.getColumn(index + 1).hidden),
          })).filter(({ hidden }) => hidden),
          monthFillColors: monthlyCollections.map(
            ({ month, fillColor: color }) => ({ month, color }),
          ),
        },
        warnings,
        errors,
      });
    }
    return rows;
  }
}
