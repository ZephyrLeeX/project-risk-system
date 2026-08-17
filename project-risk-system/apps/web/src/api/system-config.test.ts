import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublishSystemConfigRequest } from "@risk-platform/contracts";

import { SYSTEM_CONFIG_CONTRACT_ERROR } from "./system-config-contract";
import { systemConfigApi } from "./system-config";

function snapshot() {
  return {
    categories: [{ id: null, code: "DELIVERY", name: "交付风险", keywords: ["延期"], colorToken: "#4C8FE8", description: null, defaultLevel: "HIGH", sortOrder: 0, isActive: true, riskCount: 0 }],
    levels: [{ level: "HIGH", displayName: "高风险", colorToken: "#EF4444", criteria: "影响交付", keywords: ["延期"], sortOrder: 0, isActive: true }],
    aliases: [],
    mail: { syncIntervalMinutes: 30, initialSyncDays: 90, subjectKeywords: ["项目周报"], riskKeywords: ["风险"] },
    security: { sessionHours: 8, idleTimeoutMinutes: 30, loginMaxAttempts: 5, loginLockMinutes: 30, passwordMinLength: 12 },
    notifications: { mailboxSyncFailure: true, apiKeyExpiry: true, apiKeyExpiryDays: 30, importFailure: true, abnormalLogin: true },
  };
}

function overview() {
  return {
    version: "V12.3", publishedAt: "2026-08-17T00:00:00.000Z", publishedBy: "系统初始化", changeSummary: "初始配置",
    activeConfigCount: 19, activeCategoryCount: 8, activeLevelCount: 3, monthlyChangeCount: 0,
    lastMailboxSyncAt: null, nextMailboxSyncAt: null, authorizedMailboxCount: 0, snapshot: snapshot(),
  };
}

function apiResponse(data: unknown): Response {
  return new Response(JSON.stringify({ code: "OK", message: "success", data, traceId: "trace-id" }), { status: 200 });
}

afterEach(() => vi.unstubAllGlobals());

describe("system config API client", () => {
  it("loads the statistics and complete snapshot used by the editor", async () => {
    const fetch = vi.fn().mockResolvedValue(apiResponse(overview()));
    vi.stubGlobal("fetch", fetch);

    const data = await systemConfigApi.overview();

    expect(fetch).toHaveBeenCalledWith("/api/admin/system-config", expect.any(Object));
    expect(data.activeConfigCount).toBe(19);
    expect(data.snapshot.mail.subjectKeywords).toEqual(["项目周报"]);
  });

  it("rejects an overview without a snapshot using the controlled user-facing error", async () => {
    const { snapshot: _snapshot, ...invalid } = overview();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(invalid)));

    await expect(systemConfigApi.overview()).rejects.toThrow(SYSTEM_CONFIG_CONTRACT_ERROR);
  });

  it("validates the snapshot returned by publish before the editor refreshes", async () => {
    const published = { ...overview(), version: "V12.4" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(published)));
    const request = { ...snapshot(), changeCount: 1, changeSummary: "调整风险规则", module: "RISK" } as unknown as PublishSystemConfigRequest;

    const data = await systemConfigApi.publish(request);

    expect(data.version).toBe("V12.4");
    expect(data.snapshot.notifications.apiKeyExpiryDays).toBe(30);
  });
});
