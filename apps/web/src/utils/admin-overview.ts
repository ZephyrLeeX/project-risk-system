import type {
  AttentionItem,
  HealthItem,
  UnavailableSection,
} from "@/api/admin";

export type HealthStatus = HealthItem["status"];
export type AttentionStatus = AttentionItem["status"];
export type AuditResult = "SUCCESS" | "FAILURE";
export type UnavailableReason = UnavailableSection["reason"];
export type OverviewSection = UnavailableSection["section"];

/**
 * Overall health rollup for the health panel header badge. A single
 * UNAVAILABLE item dominates DEGRADED; an empty list is reported explicitly
 * so the UI can render an empty state rather than a misleading "all healthy".
 */
export type OverallHealth = "EMPTY" | "ALL_HEALTHY" | "DEGRADED" | "UNAVAILABLE";

export function overallHealthStatus(items: HealthItem[] | null): OverallHealth {
  if (!items || items.length === 0) return "EMPTY";
  const statuses = new Set(items.map((item) => item.status));
  if (statuses.has("UNAVAILABLE")) return "UNAVAILABLE";
  if (statuses.has("DEGRADED")) return "DEGRADED";
  return "ALL_HEALTHY";
}

export function overallHealthLabel(status: OverallHealth): string {
  return (
    {
      EMPTY: "暂无健康检查数据",
      ALL_HEALTHY: "全部核心服务正常",
      DEGRADED: "部分服务降级",
      UNAVAILABLE: "存在不可用服务",
    } as const
  )[status];
}

export function healthStatusLabel(status: HealthStatus): string {
  return (
    {
      HEALTHY: "正常",
      DEGRADED: "降级",
      UNAVAILABLE: "不可用",
    } as const
  )[status];
}

export function healthGlyph(status: HealthStatus): string {
  return status === "HEALTHY" ? "✓" : "!";
}

export function attentionStatusLabel(status: AttentionStatus): string {
  return status === "CRITICAL" ? "紧急" : "提醒";
}

export function attentionStatusClass(status: AttentionStatus): string {
  return status === "CRITICAL" ? "danger" : "warning";
}

export function auditResultLabel(result: AuditResult): string {
  return result === "SUCCESS" ? "成功" : "失败";
}

export function unavailableReasonLabel(reason: UnavailableReason): string {
  return (
    {
      FORBIDDEN: "无权限查看该模块",
      TIMEOUT: "该模块加载超时",
      DEPENDENCY_FAILURE: "依赖服务暂不可用",
    } as const
  )[reason];
}

export function findUnavailable(
  sections: UnavailableSection[],
  section: OverviewSection,
): UnavailableSection | undefined {
  return sections.find((item) => item.section === section);
}
