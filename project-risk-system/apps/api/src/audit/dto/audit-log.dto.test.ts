import "reflect-metadata";
import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it } from "vitest";

import { ExportAuditLogsDto, ListAuditLogsQueryDto } from "./audit-log.dto";

describe("audit log DTOs", () => {
  it("accepts the complete audit filter set and transforms pagination", async () => {
    const dto = plainToInstance(ListAuditLogsQueryDto, {
      keyword: "Trace",
      module: "CONFIG",
      action: "PUBLISH",
      result: "SUCCESS",
      dateRange: "CUSTOM",
      startDate: "2026-08-01",
      endDate: "2026-08-03",
      sensitiveOnly: "true",
      page: "2",
      pageSize: "20",
    });
    expect(await validate(dto)).toHaveLength(0);
    expect(dto.page).toBe(2);
    expect(dto.pageSize).toBe(20);
    expect(dto.sensitiveOnly).toBe(true);
  });

  it("requires a controlled export reason of at least four characters", async () => {
    const invalid = plainToInstance(ExportAuditLogsDto, {
      module: "ALL",
      action: "ALL",
      dateRange: "TODAY",
      format: "CSV",
      reason: "查",
    });
    expect((await validate(invalid)).some((error) => error.property === "reason")).toBe(true);

    const valid = plainToInstance(ExportAuditLogsDto, {
      module: "ALL",
      action: "ALL",
      dateRange: "TODAY",
      format: "XLSX",
      reason: "内控审计复核",
    });
    expect(await validate(valid)).toHaveLength(0);
  });
});
