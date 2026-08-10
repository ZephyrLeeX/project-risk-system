import "reflect-metadata";
import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it } from "vitest";

import { PublishSystemConfigDto } from "./system-config.dto";

function request() {
  return {
    categories: [
      {
        id: null,
        code: "COLLECTION",
        name: "回款风险",
        keywords: ["回款", "逾期"],
        colorToken: "#4C8FE8",
        description: "回款和应收风险",
        defaultLevel: "HIGH",
        sortOrder: 10,
        isActive: true,
      },
    ],
    levels: [
      { level: "HIGH", displayName: "高风险", colorToken: "#EF5555", criteria: "立即升级管理层处理", keywords: ["诉讼"], sortOrder: 10, isActive: true },
      { level: "MEDIUM", displayName: "中风险", colorToken: "#F0A019", criteria: "项目经理重点持续跟踪", keywords: ["延期"], sortOrder: 20, isActive: true },
      { level: "LOW", displayName: "低风险", colorToken: "#21A66D", criteria: "按项目计划持续观察", keywords: ["关注"], sortOrder: 30, isActive: true },
    ],
    aliases: [],
    mail: { syncIntervalMinutes: 30, initialSyncDays: 90, subjectKeywords: ["项目周报"], riskKeywords: ["风险"] },
    security: { sessionHours: 8, idleTimeoutMinutes: 30, loginMaxAttempts: 5, loginLockMinutes: 30, passwordMinLength: 12 },
    notifications: { mailboxSyncFailure: true, apiKeyExpiry: true, apiKeyExpiryDays: 30, importFailure: true, abnormalLogin: true },
    changeCount: 1,
    changeSummary: "调整周报同步周期",
    module: "MAIL",
  };
}

describe("PublishSystemConfigDto", () => {
  it("accepts a complete prototype configuration payload", async () => {
    const errors = await validate(plainToInstance(PublishSystemConfigDto, request()), {
      whitelist: true,
      forbidNonWhitelisted: true,
    });
    expect(errors).toHaveLength(0);
  });

  it("rejects response-only fields so frontend mapping cannot silently drift", async () => {
    const payload = request();
    Object.assign(payload.categories[0]!, { riskCount: 129 });
    const errors = await validate(plainToInstance(PublishSystemConfigDto, payload), {
      whitelist: true,
      forbidNonWhitelisted: true,
    });
    expect(errors.some((error) => error.property === "categories")).toBe(true);
  });
});
