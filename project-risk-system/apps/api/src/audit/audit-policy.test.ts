import { describe, expect, it } from "vitest";

import {
  auditActionGroup,
  auditModuleKey,
  auditSnapshotSummary,
  isSensitiveAuditEvent,
  maskClientIp,
  sanitizeAuditSnapshot,
} from "./audit-policy";

describe("audit policy", () => {
  it("normalizes stored modules and actions to the approved prototype filters", () => {
    expect(auditModuleKey("ADMIN_ROLE")).toBe("PERMISSION");
    expect(auditModuleKey("ADMIN_AI")).toBe("AI");
    expect(auditActionGroup("PROJECT_IMPORT_ROLLED_BACK")).toBe("ROLLBACK");
    expect(auditActionGroup("SYSTEM_CONFIG_PUBLISHED")).toBe("PUBLISH");
  });

  it("marks credential, permission and publication events as sensitive", () => {
    expect(isSensitiveAuditEvent("AUTH", "AUTH_PASSWORD_CHANGED")).toBe(true);
    expect(isSensitiveAuditEvent("SYSTEM_CONFIG", "SYSTEM_CONFIG_PUBLISHED")).toBe(true);
    expect(isSensitiveAuditEvent("RISK", "RISK_REOPENED")).toBe(false);
  });

  it("removes credentials and masks email addresses from snapshots", () => {
    const sanitized = sanitizeAuditSnapshot({
      apiKey: "sk-plaintext",
      password: "unsafe",
      email: "liufeng@example.com",
      result: "success",
    });
    expect(sanitized).toEqual({
      apiKey: "[已脱敏]",
      password: "[已脱敏]",
      email: "li***@example.com",
      result: "success",
    });
    const summary = auditSnapshotSummary(sanitized);
    expect(summary).not.toContain("sk-plaintext");
    expect(summary).not.toContain("unsafe");
  });

  it("keeps non-secret policy and visual token values readable", () => {
    const sanitized = sanitizeAuditSnapshot({
      passwordMinLength: 12,
      sessionHours: 8,
      colorToken: "danger-500",
      accessToken: "secret-access-token",
      levels: [{ level: "HIGH" }, { level: "LOW" }],
    });
    expect(sanitized).toEqual({
      passwordMinLength: 12,
      sessionHours: 8,
      colorToken: "danger-500",
      accessToken: "[已脱敏]",
      levels: [{ level: "HIGH" }, { level: "LOW" }],
    });
    expect(auditSnapshotSummary(sanitized)).toContain("levels：共2项");
  });

  it("masks client IP addresses before returning them to the page", () => {
    expect(maskClientIp("192.168.10.24")).toBe("192.168.*.*");
    expect(maskClientIp("::ffff:10.20.15.9")).toBe("10.20.*.*");
    expect(maskClientIp("::1")).toBe("本机");
  });
});
