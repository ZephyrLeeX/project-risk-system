import { describe, expect, it } from "vitest";

import type {
  WeeklyProjectSummary,
  WeeklyReportResponse,
} from "@/api/weekly-reports";

import {
  levelCount,
  levelCountsLabel,
  levelShortLabel,
  projectHasRisks,
  riskStatusLabel,
  staleLabel,
  summaryCount,
  todoStatusLabel,
  weekRangeLabel,
} from "@/utils/weekly-reports";

function report(overrides: Partial<WeeklyReportResponse> = {}): WeeklyReportResponse {
  return {
    weekStart: "2026-08-11",
    weekEnd: "2026-08-17",
    generatedAt: "2026-08-14T00:00:00.000Z",
    stale: false,
    freshnessDeadline: "2026-08-21T00:00:00.000Z",
    summary: {
      projectCount: 2,
      reportCount: 5,
      riskCount: 4,
      riskLevelCounts: { HIGH: 2, MEDIUM: 1, LOW: 1 },
    },
    projects: [],
    ...overrides,
  };
}

function projectSummary(riskCount: number): WeeklyProjectSummary {
  return {
    project: { id: "p", name: "P" },
    summary: {},
    riskCount,
    riskLevelCounts: {},
    sourceRevision: 1,
  };
}

describe("weekly-report presentation helpers", () => {
  it("reads numeric counts from the opaque summary map", () => {
    expect(summaryCount(report().summary, "riskCount")).toBe(4);
    expect(summaryCount(report().summary, "projectCount")).toBe(2);
    expect(summaryCount(null, "riskCount")).toBeNull();
    expect(summaryCount({ riskCount: "3" }, "riskCount")).toBe(3);
    expect(summaryCount({ riskCount: "n/a" }, "riskCount")).toBeNull();
  });

  it("reads level counts defensively across number/string shapes", () => {
    expect(levelCount({ HIGH: 2 }, "HIGH")).toBe(2);
    expect(levelCount({ HIGH: "2" }, "HIGH")).toBe(2);
    expect(levelCount({ HIGH: "x" }, "HIGH")).toBeNull();
    expect(levelCount(null, "HIGH")).toBeNull();
    expect(levelCount({}, "HIGH")).toBeNull();
  });

  it("renders a level-counts line skipping unknown levels", () => {
    expect(levelCountsLabel({ HIGH: 2, MEDIUM: 1, LOW: 1 })).toBe(
      "高 2 · 中 1 · 低 1",
    );
    expect(levelCountsLabel({ HIGH: 2 })).toBe("高 2");
    expect(levelCountsLabel({})).toBe("暂无风险等级分布");
    expect(levelCountsLabel(null)).toBe("暂无风险等级分布");
  });

  it("labels short risk levels", () => {
    expect(levelShortLabel("HIGH")).toBe("高");
    expect(levelShortLabel("UNKNOWN")).toBe("待确认");
  });

  it("renders the week range from the report", () => {
    expect(weekRangeLabel(report())).toBe("2026-08-11 ~ 2026-08-17");
    expect(weekRangeLabel(null)).toBe("暂无周报");
  });

  it("labels the stale flag", () => {
    expect(staleLabel(false)).toBe("数据已就绪");
    expect(staleLabel(true)).toBe("部分数据可能已过期，正在重建");
    expect(staleLabel(null)).toBe("数据已就绪");
  });

  it("maps risk and todo statuses", () => {
    expect(riskStatusLabel("ACTIVE")).toBe("未解除");
    expect(riskStatusLabel("RESOLVED")).toBe("已解除");
    expect(todoStatusLabel("PENDING")).toBe("待处理");
    expect(todoStatusLabel("IN_PROGRESS")).toBe("处理中");
    expect(todoStatusLabel("COMPLETED")).toBe("已完成");
  });

  it("knows whether a project has risks to open", () => {
    expect(projectHasRisks(projectSummary(3))).toBe(true);
    expect(projectHasRisks(projectSummary(0))).toBe(false);
  });
});
