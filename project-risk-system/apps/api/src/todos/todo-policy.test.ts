import { describe, expect, it } from "vitest";

import type { ManagerTodoItem } from "@risk-platform/contracts";

import {
  buildScheduleSuggestions,
  defaultAssigneeForRisk,
  urgencyForRisk,
} from "./todo-policy";

function item(
  id: string,
  dueDate: string | null = null,
): ManagerTodoItem {
  return {
    id,
    riskId: id,
    projectId: id,
    projectName: `项目${id}`,
    projectOwnerName: "王经理",
    departmentName: "交付部",
    title: `处理事项${id}`,
    description: "核实风险并明确下一步。",
    urgency: id === "1" ? "EMERGENCY" : "HIGH",
    status: "PENDING",
    sourceType: "RISK_SUGGESTION",
    typeLabel: "回款风险",
    assigneeUserId: null,
    assigneeName: "管理者",
    dueDate,
    completionNote: null,
    completedAt: null,
    createdAt: "2026-07-31T00:00:00.000Z",
    updatedAt: "2026-07-31T00:00:00.000Z",
  };
}

describe("todo policy", () => {
  it("maps risk levels to action urgency", () => {
    expect(urgencyForRisk("HIGH")).toBe("EMERGENCY");
    expect(urgencyForRisk("MEDIUM")).toBe("HIGH");
    expect(urgencyForRisk("LOW")).toBe("NORMAL");
    expect(urgencyForRisk("UNKNOWN")).toBe("NORMAL");
  });

  it("assigns high risks to management and others to project owners", () => {
    expect(defaultAssigneeForRisk("HIGH", "王经理")).toBe("管理者");
    expect(defaultAssigneeForRisk("MEDIUM", "王经理")).toBe("王经理");
    expect(defaultAssigneeForRisk("LOW", null)).toBe("管理者");
  });

  it("builds a Monday to Friday schedule and preserves dates in this week", () => {
    const schedule = buildScheduleSuggestions(
      [item("1", "2026-07-30"), item("2")],
      new Date("2026-07-31T08:00:00.000Z"),
    );
    expect(schedule[0]).toMatchObject({
      weekday: "周四",
      date: "2026-07-30",
    });
    expect(schedule[1]).toMatchObject({
      weekday: "周二",
      date: "2026-07-28",
    });
  });

  it("excludes completed items and limits the schedule to five entries", () => {
    const completed = { ...item("0"), status: "COMPLETED" as const };
    const schedule = buildScheduleSuggestions(
      [completed, ...[1, 2, 3, 4, 5, 6].map((value) => item(String(value)))],
      new Date("2026-07-31T08:00:00.000Z"),
    );
    expect(schedule).toHaveLength(5);
    expect(schedule.some(({ actionItemId }) => actionItemId === "0")).toBe(false);
  });
});
