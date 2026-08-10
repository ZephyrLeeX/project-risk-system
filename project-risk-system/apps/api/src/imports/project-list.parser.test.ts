import { describe, expect, it } from "vitest";
import ExcelJS = require("exceljs");

import { ProjectListParser } from "./project-list.parser";

async function sampleWorkbook(): Promise<Buffer> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("数据回款");
  sheet.getRow(2).getCell(8).value = "实际";
  sheet.getRow(2).getCell(9).value = "预计";
  const headers = [
    "交付部门",
    "交付负责人",
    "项目编码",
    "项目名称",
    "2026年计划滚测小计",
    "实际已回款",
    "剩余待回款",
    "1月",
    "2月",
    "3月",
    "4月",
    "5月",
    "6月",
    "7月",
    "8月",
    "9月",
    "10月",
    "11月",
    "12月",
    "回款风险",
    "回款进展",
  ];
  headers.forEach((header, index) => {
    sheet.getRow(3).getCell(index + 1).value = header;
  });
  const firstRow = [
    "项目交付一部",
    "王绍华",
    null,
    "重复名称",
    null,
    null,
    null,
    null,
    0,
    ...Array.from({ length: 10 }, () => null),
    "高",
    "跟进中",
  ];
  firstRow.forEach((value, index) => {
    sheet.getRow(4).getCell(index + 1).value = value;
  });
  sheet.getRow(4).getCell(8).fill = {
    type: "pattern",
    pattern: "solid",
    fgColor: { argb: "FFFF0000" },
  };
  const secondRow = [
    "项目交付二部",
    "付瑞强",
    "CODE-2",
    "重复名称",
    100,
    40,
    60,
    ...Array.from({ length: 12 }, () => 0),
    "低",
    "正常",
  ];
  secondRow.forEach((value, index) => {
    sheet.getRow(5).getCell(index + 1).value = value;
  });
  workbook.addWorksheet("汇总");

  const bytes = await workbook.xlsx.writeBuffer();
  return Buffer.from(bytes as unknown as Uint8Array);
}

async function workbookWithSupplementalSheet(): Promise<Buffer> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(
    (await sampleWorkbook()) as unknown as Parameters<
      typeof workbook.xlsx.load
    >[0],
  );
  const sheet = workbook.addWorksheet("涵谷回款");
  ["实际", "实际", "实际", "实际", "实际", "计划"].forEach(
    (attribute, index) => {
      sheet.getRow(1).getCell(11 + index).value = attribute;
    },
  );
  const headers = [
    "项目编码",
    "项目名称",
    "合同应收金额",
    "采购合同总额",
    "累计已收款额",
    "剩余未回款",
    "26年实际回款",
    "26年实际回款净额",
    "26年回款计划",
    "回款风险",
    "1月",
    "2月",
    "3月",
    "4月",
    "5月",
    "6月",
    "7月",
    "8月",
    "9月",
    "10月",
    "11月",
    "12月",
    "26年以后",
  ];
  headers.forEach((header, index) => {
    sheet.getRow(2).getCell(index + 1).value = header;
  });
  sheet.getColumn(3).hidden = true;
  sheet.getColumn(4).hidden = true;
  sheet.getColumn(5).hidden = true;
  [
    null,
    "未匹配的补充项目",
    1000,
    300,
    450,
    550,
    100,
    90,
    500,
    "低",
    ...Array.from({ length: 12 }, (_, index) => index * 10),
    200,
  ].forEach((value, index) => {
    sheet.getRow(3).getCell(index + 1).value = value;
  });
  const bytes = await workbook.xlsx.writeBuffer();
  return Buffer.from(bytes as unknown as Uint8Array);
}

async function workbookWithLegalSheet(): Promise<Buffer> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(
    (await sampleWorkbook()) as unknown as Parameters<
      typeof workbook.xlsx.load
    >[0],
  );
  const sheet = workbook.addWorksheet("发函-诉讼清单");
  ["实际", "实际", "实际", "实际", "实际", "预计"].forEach(
    (attribute, index) => {
      sheet.getRow(2).getCell(6 + index).value = attribute;
    },
  );
  const headers = [
    "交付部门",
    "交付负责人",
    "项目编码",
    "项目名称",
    "2026年计划滚测小计",
    "1月",
    "2月",
    "3月",
    "4月",
    "5月",
    "6月",
    "7月",
    "8月",
    "9月",
    "10月",
    "11月",
    "12月",
    "回款风险",
    "回款进展",
  ];
  headers.forEach((header, index) => {
    sheet.getRow(3).getCell(index + 1).value = header;
  });
  [
    "项目交付一部",
    "王绍华",
    null,
    "重复名称",
    100,
    ...Array.from({ length: 12 }, (_, index) => index),
    "高",
    "已发律师函，准备起诉",
  ].forEach((value, index) => {
    sheet.getRow(4).getCell(index + 1).value = value;
  });
  const bytes = await workbook.xlsx.writeBuffer();
  return Buffer.from(bytes as unknown as Uint8Array);
}

async function workbookWithDuplicateProjectCode(): Promise<Buffer> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(
    (await sampleWorkbook()) as unknown as Parameters<
      typeof workbook.xlsx.load
    >[0],
  );
  const sheet = workbook.getWorksheet("数据回款")!;
  sheet.getRow(4).getCell(3).value = "CODE-2";
  const bytes = await workbook.xlsx.writeBuffer();
  return Buffer.from(bytes as unknown as Uint8Array);
}

describe("ProjectListParser", () => {
  it("preserves blank amounts, explicit zero, month attributes and source colors", async () => {
    const parsed = await new ProjectListParser().parse(
      await sampleWorkbook(),
    );

    expect(parsed.sheetNames).toEqual(["数据回款", "汇总"]);
    expect(parsed.monthAttributes["1"]).toBe("实际");
    expect(parsed.monthAttributes["2"]).toBe("预计");
    expect(parsed.rows).toHaveLength(2);
    expect(parsed.rows[0]?.annualPlanAmount).toBeNull();
    expect(parsed.rows[0]?.monthlyCollections[0]?.amount).toBeNull();
    expect(parsed.rows[0]?.monthlyCollections[0]?.fillColor).toBe(
      "FFFF0000",
    );
    expect(parsed.rows[0]?.monthlyCollections[1]?.amount).toBe("0");
    expect(parsed.rows[1]?.annualPlanAmount).toBe("100");
    expect(parsed.rows[1]?.actualCollectedAmount).toBe("40");
    expect(parsed.rows[1]?.remainingAmount).toBe("60");
    expect(parsed.rows[0]?.collectionRiskLevel).toBe("HIGH");
    expect(parsed.rows[1]?.collectionRiskLevel).toBe("LOW");
  });

  it("marks missing codes and duplicate project names as warnings", async () => {
    const parsed = await new ProjectListParser().parse(
      await sampleWorkbook(),
    );

    expect(parsed.rows[0]?.status).toBe("WARNING");
    expect(parsed.rows[0]?.warnings).toContain(
      "项目编码为空，将使用项目名称、部门和负责人组合匹配",
    );
    expect(parsed.rows[0]?.warnings).toContain(
      "同一文件存在重名项目，确认导入前请核对",
    );
    expect(parsed.rows[1]?.warnings).toContain(
      "同一文件存在重名项目，确认导入前请核对",
    );
    expect(parsed.rows[0]?.importKey).not.toBe(
      parsed.rows[1]?.importKey,
    );
  });

  it("keeps the first duplicate code canonical and merges later rows", async () => {
    const parsed = await new ProjectListParser().parse(
      await workbookWithDuplicateProjectCode(),
    );

    expect(parsed.rows[0]?.action).toBe("CREATE");
    expect(parsed.rows[1]?.action).toBe("UPDATE");
    expect(parsed.rows[0]?.importKey).not.toBe(parsed.rows[1]?.importKey);
    expect(parsed.rows[0]?.warnings).toContain(
      "同一文件项目编码重复，后续重复行将合并到本项目",
    );
    expect(parsed.rows[1]?.warnings).toContain(
      "同一文件项目编码重复，确认后将合并更新同一项目",
    );
  });

  it("rejects a workbook without the required main sheet", async () => {
    const workbook = new ExcelJS.Workbook();
    workbook.addWorksheet("其他");
    const bytes = await workbook.xlsx.writeBuffer();

    await expect(
      new ProjectListParser().parse(
        Buffer.from(bytes as unknown as Uint8Array),
      ),
    ).rejects.toThrow("未找到“数据回款”工作表");
  });

  it("reads supplemental collection rows including hidden amount columns", async () => {
    const parsed = await new ProjectListParser().parse(
      await workbookWithSupplementalSheet(),
    );

    expect(parsed.supplementalCollections).toHaveLength(1);
    const row = parsed.supplementalCollections[0]!;
    expect(row.projectName).toBe("未匹配的补充项目");
    expect(row.contractReceivableAmount).toBe("1000");
    expect(row.procurementContractAmount).toBe("300");
    expect(row.cumulativeCollectedAmount).toBe("450");
    expect(row.monthlyCollections[0]?.amount).toBe("0");
    expect(row.monthlyCollections[5]?.attribute).toBe("计划");
    expect(row.afterYearAmount).toBe("200");
    expect(row.collectionRiskLevel).toBe("LOW");
    expect(row.sourceSnapshot.hiddenColumns).toEqual([
      { column: 3, hidden: true },
      { column: 4, hidden: true },
      { column: 5, hidden: true },
    ]);
  });

  it("reads legal matter rows and preserves legal progress", async () => {
    const parsed = await new ProjectListParser().parse(
      await workbookWithLegalSheet(),
    );

    expect(parsed.legalMatters).toHaveLength(1);
    const row = parsed.legalMatters[0]!;
    expect(row.projectName).toBe("重复名称");
    expect(row.departmentName).toBe("项目交付一部");
    expect(row.deliveryOwnerName).toBe("王绍华");
    expect(row.annualPlanAmount).toBe("100");
    expect(row.monthlyCollections[0]?.amount).toBe("0");
    expect(row.monthlyCollections[5]?.attribute).toBe("预计");
    expect(row.collectionRiskLevel).toBe("HIGH");
    expect(row.legalProgress).toBe("已发律师函，准备起诉");
    expect(row.status).toBe("WARNING");
  });
});
