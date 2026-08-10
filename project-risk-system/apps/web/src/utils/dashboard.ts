import type {
  ProjectRiskLevel,
  RiskSourceType,
} from "@risk-platform/contracts";

export function formatWan(
  valueYuan: string | null | undefined,
): string {
  if (valueYuan === null || valueYuan === undefined) return "数据待补充";
  const value = Number(valueYuan);
  if (!Number.isFinite(value)) return "数据待补充";
  return `${new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value / 10_000)} 万`;
}

export function levelLabel(level: ProjectRiskLevel): string {
  return (
    {
      HIGH: "高风险",
      MEDIUM: "中风险",
      LOW: "低风险",
      UNKNOWN: "待确认",
    } as const
  )[level];
}

export function sourceLabel(source: RiskSourceType): string {
  return (
    {
      EXCEL: "项目清单 Excel",
      LITIGATION: "发函诉讼清单",
      MAIL_AI: "周报邮件 AI 提炼",
      MANUAL: "日常上报",
    } as const
  )[source];
}

export function formatDateTime(value: string | null): string {
  if (!value) return "暂无更新时间";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
