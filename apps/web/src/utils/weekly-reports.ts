import type {
  ProjectRiskLevel,
} from "@risk-platform/contracts";

import type {
  WeeklyProjectSummary,
  WeeklyReportItemResponse,
  WeeklyReportResponse,
} from "@/api/weekly-reports";

/**
 * Weekly-report presentation helpers (ADR 0021).
 *
 * The frozen contract types the aggregate `summary` and per-project
 * `riskLevelCounts` as opaque `{[key: string]: JSONValue}` maps, so these
 * helpers narrow them at runtime rather than assuming keys. They never
 * fabricate data: an unknown/missing value renders an explicit placeholder.
 */

/** A safe level-count entry, or `null` when the map does not expose the level. */
export function levelCount(
  counts: Record<string, unknown> | null | undefined,
  level: ProjectRiskLevel,
): number | null {
  if (!counts) return null;
  const value = counts[level];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

/** Render a level-counts line like "高 2 · 中 3 · 低 1", skipping unknowns. */
export function levelCountsLabel(
  counts: Record<string, unknown> | null | undefined,
): string {
  const entries: Array<[string, number]> = [];
  (["HIGH", "MEDIUM", "LOW", "UNKNOWN"] as const).forEach((level) => {
    const count = levelCount(counts, level);
    if (count !== null) entries.push([levelShortLabel(level), count]);
  });
  if (!entries.length) return "暂无风险等级分布";
  return entries.map(([label, count]) => `${label} ${count}`).join(" · ");
}

export function levelShortLabel(level: ProjectRiskLevel): string {
  return (
    {
      HIGH: "高",
      MEDIUM: "中",
      LOW: "低",
      UNKNOWN: "待确认",
    } as const
  )[level];
}

/** Read a numeric summary field from the opaque aggregate summary map. */
export function summaryCount(
  summary: Record<string, unknown> | null | undefined,
  key: "projectCount" | "reportCount" | "riskCount",
): number | null {
  if (!summary) return null;
  const value = summary[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

/** "2026-08-11 ~ 2026-08-17" style week range, or a placeholder. */
export function weekRangeLabel(report: WeeklyReportResponse | null): string {
  if (!report) return "暂无周报";
  return `${report.weekStart} ~ ${report.weekEnd}`;
}

/** Human label for the stale flag. */
export function staleLabel(stale: boolean | null | undefined): string {
  return stale ? "部分数据可能已过期，正在重建" : "数据已就绪";
}

/** Map a risk item's status to a display label. */
export function riskStatusLabel(
  status: WeeklyReportItemResponse["riskStatus"],
): string {
  return ({ ACTIVE: "未解除", RESOLVED: "已解除" } as const)[status];
}

/** Map a risk item's todo status to a display label. */
export function todoStatusLabel(
  status: WeeklyReportItemResponse["todoStatus"],
): string {
  return (
    {
      PENDING: "待处理",
      IN_PROGRESS: "处理中",
      COMPLETED: "已完成",
    } as const
  )[status];
}

/** Whether a project summary has any risks worth opening for detail. */
export function projectHasRisks(project: WeeklyProjectSummary): boolean {
  return project.riskCount > 0;
}
