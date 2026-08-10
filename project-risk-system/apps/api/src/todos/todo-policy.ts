import type {
  ActionItemUrgency,
  ManagerTodoItem,
  ManagerTodoScheduleItem,
  ProjectRiskLevel,
} from "@risk-platform/contracts";

const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五"] as const;

export function urgencyForRisk(
  level: ProjectRiskLevel,
): ActionItemUrgency {
  if (level === "HIGH") return "EMERGENCY";
  if (level === "MEDIUM") return "HIGH";
  return "NORMAL";
}

export function defaultAssigneeForRisk(
  level: ProjectRiskLevel,
  ownerName: string | null | undefined,
): string {
  return level === "HIGH" ? "管理者" : ownerName?.trim() || "管理者";
}

export function buildScheduleSuggestions(
  items: ManagerTodoItem[],
  now = new Date(),
): ManagerTodoScheduleItem[] {
  const monday = startOfWorkWeek(now);
  const active = items
    .filter((item) => item.status !== "COMPLETED")
    .slice(0, WEEKDAYS.length);

  return active.map((item, index) => {
    const suggestedDate = new Date(monday);
    suggestedDate.setUTCDate(monday.getUTCDate() + index);
    const explicitDate = item.dueDate ? parseDate(item.dueDate) : null;
    const date =
      explicitDate && inWorkWeek(explicitDate, monday)
        ? explicitDate
        : suggestedDate;
    const weekdayIndex = Math.min(
      Math.max(0, weekdayOffset(date, monday)),
      WEEKDAYS.length - 1,
    );

    return {
      weekday: WEEKDAYS[weekdayIndex]!,
      date: toDateString(date),
      actionItemId: item.id,
      title: item.title,
      projectName: item.projectName,
      assigneeName: item.assigneeName,
      urgency: item.urgency,
    };
  });
}

function startOfWorkWeek(value: Date): Date {
  const date = new Date(
    Date.UTC(
      value.getUTCFullYear(),
      value.getUTCMonth(),
      value.getUTCDate(),
    ),
  );
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() - day + 1);
  return date;
}

function parseDate(value: string): Date | null {
  const date = new Date(`${value.slice(0, 10)}T00:00:00.000Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function inWorkWeek(date: Date, monday: Date): boolean {
  const offset = weekdayOffset(date, monday);
  return offset >= 0 && offset < WEEKDAYS.length;
}

function weekdayOffset(date: Date, monday: Date): number {
  return Math.floor((date.getTime() - monday.getTime()) / 86_400_000);
}

function toDateString(date: Date): string {
  return date.toISOString().slice(0, 10);
}
