import { describe, expect, it } from "vitest";

import type { HealthItem, UnavailableSection } from "@/api/admin";

import {
  attentionStatusClass,
  attentionStatusLabel,
  auditResultLabel,
  findUnavailable,
  healthGlyph,
  healthStatusLabel,
  overallHealthLabel,
  overallHealthStatus,
  unavailableReasonLabel,
} from "@/utils/admin-overview";

function healthItem(status: HealthItem["status"]): HealthItem {
  return {
    key: "DATABASE",
    label: "数据库",
    status,
    checkedAt: "2026-08-12T01:02:03.123Z",
    summary: "数据库连接正常",
    code: status === "HEALTHY" ? null : "TIMEOUT",
    link: null,
  };
}

describe("admin overview presentation helpers", () => {
  it("rolls up the overall health badge across items", () => {
    expect(overallHealthStatus(null)).toBe("EMPTY");
    expect(overallHealthStatus([])).toBe("EMPTY");
    expect(overallHealthStatus([healthItem("HEALTHY")])).toBe("ALL_HEALTHY");
    expect(
      overallHealthStatus([healthItem("HEALTHY"), healthItem("DEGRADED")]),
    ).toBe("DEGRADED");
    expect(
      overallHealthStatus([healthItem("HEALTHY"), healthItem("UNAVAILABLE")]),
    ).toBe("UNAVAILABLE");
    // UNAVAILABLE dominates DEGRADED.
    expect(
      overallHealthStatus([healthItem("DEGRADED"), healthItem("UNAVAILABLE")]),
    ).toBe("UNAVAILABLE");
  });

  it("maps overall health to human badges", () => {
    expect(overallHealthLabel("ALL_HEALTHY")).toBe("全部核心服务正常");
    expect(overallHealthLabel("UNAVAILABLE")).toBe("存在不可用服务");
    expect(overallHealthLabel("EMPTY")).toBe("暂无健康检查数据");
  });

  it("labels per-item health status and glyph", () => {
    expect(healthStatusLabel("HEALTHY")).toBe("正常");
    expect(healthStatusLabel("DEGRADED")).toBe("降级");
    expect(healthStatusLabel("UNAVAILABLE")).toBe("不可用");
    expect(healthGlyph("HEALTHY")).toBe("✓");
    expect(healthGlyph("UNAVAILABLE")).toBe("!");
  });

  it("labels attention severity with matching tone class", () => {
    expect(attentionStatusLabel("CRITICAL")).toBe("紧急");
    expect(attentionStatusLabel("WARNING")).toBe("提醒");
    expect(attentionStatusClass("CRITICAL")).toBe("danger");
    expect(attentionStatusClass("WARNING")).toBe("warning");
  });

  it("labels audit results", () => {
    expect(auditResultLabel("SUCCESS")).toBe("成功");
    expect(auditResultLabel("FAILURE")).toBe("失败");
  });

  it("maps unavailable section reasons to safe display text", () => {
    expect(unavailableReasonLabel("FORBIDDEN")).toBe("无权限查看该模块");
    expect(unavailableReasonLabel("TIMEOUT")).toBe("该模块加载超时");
    expect(unavailableReasonLabel("DEPENDENCY_FAILURE")).toBe("依赖服务暂不可用");
  });

  it("finds the unavailable marker for a given section", () => {
    const sections: UnavailableSection[] = [
      { section: "health", reason: "FORBIDDEN", code: "FORBIDDEN" },
      { section: "recentAudit", reason: "TIMEOUT", code: "TIMEOUT" },
    ];
    expect(findUnavailable(sections, "health")?.reason).toBe("FORBIDDEN");
    expect(findUnavailable(sections, "recentAudit")?.reason).toBe("TIMEOUT");
    expect(findUnavailable(sections, "attention")).toBeUndefined();
    expect(findUnavailable([], "health")).toBeUndefined();
  });
});
