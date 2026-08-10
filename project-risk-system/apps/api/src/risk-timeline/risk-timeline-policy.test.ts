import {
  buildActionTimelineChange,
  eventPresentation,
} from "./risk-timeline-policy";
import { describe, expect, it } from "vitest";

describe("risk timeline policy", () => {
  it("marks a completed action as a completion event", () => {
    expect(
      buildActionTimelineChange(
        {
          status: "IN_PROGRESS",
          assigneeName: "刘峰",
          dueDate: null,
          completionNote: null,
        },
        {
          status: "COMPLETED",
          assigneeName: "刘峰",
          dueDate: null,
          completionNote: "已完成沟通",
        },
      ),
    ).toMatchObject({
      eventType: "ACTION_COMPLETED",
      fromValue: "IN_PROGRESS",
      toValue: "COMPLETED",
    });
  });

  it("describes assignment and due date changes", () => {
    const change = buildActionTimelineChange(
      {
        status: "PENDING",
        assigneeName: "待分配",
        dueDate: null,
        completionNote: null,
      },
      {
        status: "PENDING",
        assigneeName: "王绍华",
        dueDate: "2026-08-05",
        completionNote: null,
      },
    );
    expect(change.eventType).toBe("ACTION_UPDATED");
    expect(change.description).toContain("王绍华");
    expect(change.description).toContain("2026-08-05");
  });

  it("uses a green presentation for resolved events", () => {
    expect(eventPresentation("RISK_RESOLVED")).toEqual({
      label: "风险解除",
      tone: "GREEN",
    });
  });
});
