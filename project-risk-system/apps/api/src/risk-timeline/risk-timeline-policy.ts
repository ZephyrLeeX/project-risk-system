import type {
  ActionItemStatus,
  RiskTimelineEventType,
} from "@prisma/client";

export interface ActionTimelineSnapshot {
  status: ActionItemStatus;
  assigneeName: string | null;
  dueDate: string | null;
  completionNote: string | null;
}

export interface ActionTimelineChange {
  eventType: RiskTimelineEventType;
  title: string;
  description: string;
  fromValue: string | null;
  toValue: string | null;
}

const STATUS_LABEL: Record<ActionItemStatus, string> = {
  PENDING: "待处理",
  IN_PROGRESS: "处理中",
  COMPLETED: "已完成",
};

export function buildActionTimelineChange(
  before: ActionTimelineSnapshot,
  after: ActionTimelineSnapshot,
): ActionTimelineChange {
  if (before.status !== after.status) {
    const completed = after.status === "COMPLETED";
    return {
      eventType: completed
        ? "ACTION_COMPLETED"
        : "ACTION_STATUS_CHANGED",
      title: completed ? "待办事项已完成" : "待办处理状态更新",
      description: completed
        ? `待办由“${STATUS_LABEL[before.status]}”变更为“已完成”${after.completionNote ? `：${after.completionNote}` : "。"}`
        : `待办由“${STATUS_LABEL[before.status]}”变更为“${STATUS_LABEL[after.status]}”。`,
      fromValue: before.status,
      toValue: after.status,
    };
  }

  const changes: string[] = [];
  if (before.assigneeName !== after.assigneeName) {
    changes.push(
      `负责人由“${before.assigneeName || "待分配"}”调整为“${after.assigneeName || "待分配"}”`,
    );
  }
  if (before.dueDate !== after.dueDate) {
    changes.push(
      `截止日期由“${before.dueDate || "待安排"}”调整为“${after.dueDate || "待安排"}”`,
    );
  }
  if (before.completionNote !== after.completionNote) {
    changes.push("处理说明已更新");
  }
  return {
    eventType: "ACTION_UPDATED",
    title: "待办事项信息更新",
    description: changes.length ? `${changes.join("；")}。` : "待办事项已更新。",
    fromValue: null,
    toValue: null,
  };
}

export function eventPresentation(
  eventType: RiskTimelineEventType,
): {
  label: string;
  tone: "RED" | "ORANGE" | "BLUE" | "GREEN" | "GRAY";
} {
  const presentations: Record<
    RiskTimelineEventType,
    {
      label: string;
      tone: "RED" | "ORANGE" | "BLUE" | "GREEN" | "GRAY";
    }
  > = {
    RISK_CREATED: { label: "新增风险", tone: "RED" },
    RISK_UPDATED: { label: "风险更新", tone: "BLUE" },
    LEVEL_CHANGED: { label: "等级变化", tone: "ORANGE" },
    ACTION_CREATED: { label: "生成待办", tone: "BLUE" },
    ACTION_UPDATED: { label: "待办更新", tone: "BLUE" },
    ACTION_STATUS_CHANGED: { label: "处理推进", tone: "ORANGE" },
    ACTION_COMPLETED: { label: "待办完成", tone: "GREEN" },
    RISK_RESOLVED: { label: "风险解除", tone: "GREEN" },
    RISK_REOPENED: { label: "风险重启", tone: "RED" },
  };
  return presentations[eventType];
}
