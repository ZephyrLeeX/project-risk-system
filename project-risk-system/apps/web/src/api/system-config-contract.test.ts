import { describe, expect, it } from "vitest";

import {
  cloneConfigSnapshot,
  requireSystemConfigOverview,
  SYSTEM_CONFIG_CONTRACT_ERROR,
} from "./system-config-contract";

function snapshot() {
  return {
    categories: [{
      id: null,
      code: "DELIVERY",
      name: "交付风险",
      keywords: ["延期"],
      colorToken: "#4C8FE8",
      description: null,
      defaultLevel: "HIGH",
      sortOrder: 0,
      isActive: true,
      riskCount: 0,
    }],
    levels: [{
      level: "HIGH",
      displayName: "高风险",
      colorToken: "#EF4444",
      criteria: "影响交付",
      keywords: ["延期"],
      sortOrder: 0,
      isActive: true,
    }],
    aliases: [],
    mail: {
      syncIntervalMinutes: 30,
      initialSyncDays: 90,
      subjectKeywords: ["项目周报"],
      riskKeywords: ["风险"],
    },
    security: {
      sessionHours: 8,
      idleTimeoutMinutes: 30,
      loginMaxAttempts: 5,
      loginLockMinutes: 30,
      passwordMinLength: 12,
    },
    notifications: {
      mailboxSyncFailure: true,
      apiKeyExpiry: true,
      apiKeyExpiryDays: 30,
      importFailure: true,
      abnormalLogin: true,
    },
  };
}

function overview() {
  return {
    version: "V12.3",
    publishedAt: "2026-08-17T00:00:00.000Z",
    publishedBy: "系统初始化",
    changeSummary: "初始配置",
    activeConfigCount: 19,
    activeCategoryCount: 8,
    activeLevelCount: 3,
    monthlyChangeCount: 0,
    lastMailboxSyncAt: null,
    nextMailboxSyncAt: null,
    authorizedMailboxCount: 0,
    snapshot: snapshot(),
  };
}

describe("system config overview contract", () => {
  it("accepts an overview with a snapshot and creates an independent editor draft", () => {
    const response = requireSystemConfigOverview(overview());
    const draft = cloneConfigSnapshot(response.snapshot);

    draft.mail.syncIntervalMinutes = 60;
    expect(response.snapshot.mail.syncIntervalMinutes).toBe(30);
    expect(draft.mail.syncIntervalMinutes).toBe(60);
  });

  it("fails closed with a controlled error when the snapshot is absent", () => {
    const { snapshot: _snapshot, ...invalid } = overview();

    expect(() => requireSystemConfigOverview(invalid)).toThrow(
      SYSTEM_CONFIG_CONTRACT_ERROR,
    );
    expect(() => cloneConfigSnapshot(undefined)).toThrow(
      SYSTEM_CONFIG_CONTRACT_ERROR,
    );
  });

  it("fails closed when a nested required snapshot field is absent", () => {
    const invalid = overview();
    delete (invalid.snapshot.mail as Partial<typeof invalid.snapshot.mail>)
      .subjectKeywords;

    expect(() => requireSystemConfigOverview(invalid)).toThrow(
      SYSTEM_CONFIG_CONTRACT_ERROR,
    );
  });

  it("validates the snapshot returned after publishing before refreshing the draft", () => {
    const publishResponse = requireSystemConfigOverview(overview());

    expect(cloneConfigSnapshot(publishResponse.snapshot)).toEqual(snapshot());
  });
});
