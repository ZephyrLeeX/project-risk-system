import type {
  SystemConfigOverview,
  SystemConfigSnapshot,
} from "@risk-platform/contracts";

export const SYSTEM_CONFIG_CONTRACT_ERROR = "系统配置数据格式无效，请联系管理员";

export class SystemConfigContractError extends Error {
  constructor() {
    super(SYSTEM_CONFIG_CONTRACT_ERROR);
    this.name = "SystemConfigContractError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStringOrNull(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRiskCategory(value: unknown): boolean {
  return isRecord(value) &&
    isStringOrNull(value.id) &&
    typeof value.code === "string" &&
    typeof value.name === "string" &&
    isStringArray(value.keywords) &&
    typeof value.colorToken === "string" &&
    isStringOrNull(value.description) &&
    isStringOrNull(value.defaultLevel) &&
    typeof value.sortOrder === "number" &&
    typeof value.isActive === "boolean" &&
    typeof value.riskCount === "number";
}

function isRiskLevel(value: unknown): boolean {
  return isRecord(value) &&
    ["HIGH", "MEDIUM", "LOW"].includes(value.level as string) &&
    typeof value.displayName === "string" &&
    typeof value.colorToken === "string" &&
    typeof value.criteria === "string" &&
    isStringArray(value.keywords) &&
    typeof value.sortOrder === "number" &&
    typeof value.isActive === "boolean";
}

function isProjectAlias(value: unknown): boolean {
  return isRecord(value) &&
    isStringOrNull(value.id) &&
    typeof value.projectId === "string" &&
    typeof value.projectName === "string" &&
    isStringOrNull(value.projectCode) &&
    isStringOrNull(value.projectOwnerName) &&
    typeof value.alias === "string" &&
    typeof value.source === "string" &&
    isStringOrNull(value.note) &&
    typeof value.isActive === "boolean" &&
    typeof value.hitCount === "number" &&
    isStringOrNull(value.lastHitAt);
}

function isMailSettings(value: unknown): boolean {
  return isRecord(value) &&
    typeof value.syncIntervalMinutes === "number" &&
    typeof value.initialSyncDays === "number" &&
    isStringArray(value.subjectKeywords) &&
    isStringArray(value.riskKeywords);
}

function isSecuritySettings(value: unknown): boolean {
  return isRecord(value) &&
    typeof value.sessionHours === "number" &&
    typeof value.idleTimeoutMinutes === "number" &&
    typeof value.loginMaxAttempts === "number" &&
    typeof value.loginLockMinutes === "number" &&
    typeof value.passwordMinLength === "number";
}

function isNotificationSettings(value: unknown): boolean {
  return isRecord(value) &&
    typeof value.mailboxSyncFailure === "boolean" &&
    typeof value.apiKeyExpiry === "boolean" &&
    typeof value.apiKeyExpiryDays === "number" &&
    typeof value.importFailure === "boolean" &&
    typeof value.abnormalLogin === "boolean";
}

function isConfigSnapshot(value: unknown): value is SystemConfigSnapshot {
  if (!isRecord(value)) return false;

  return (
    Array.isArray(value.categories) && value.categories.every(isRiskCategory) &&
    Array.isArray(value.levels) && value.levels.every(isRiskLevel) &&
    Array.isArray(value.aliases) && value.aliases.every(isProjectAlias) &&
    isMailSettings(value.mail) &&
    isSecuritySettings(value.security) &&
    isNotificationSettings(value.notifications)
  );
}

export function requireSystemConfigOverview(value: unknown): SystemConfigOverview {
  if (!isRecord(value) ||
    typeof value.version !== "string" ||
    typeof value.publishedAt !== "string" ||
    typeof value.publishedBy !== "string" ||
    typeof value.changeSummary !== "string" ||
    typeof value.activeConfigCount !== "number" ||
    typeof value.activeCategoryCount !== "number" ||
    typeof value.activeLevelCount !== "number" ||
    typeof value.monthlyChangeCount !== "number" ||
    !isStringOrNull(value.lastMailboxSyncAt) ||
    !isStringOrNull(value.nextMailboxSyncAt) ||
    typeof value.authorizedMailboxCount !== "number" ||
    !isConfigSnapshot(value.snapshot)) {
    throw new SystemConfigContractError();
  }

  return value as unknown as SystemConfigOverview;
}

export function cloneConfigSnapshot(value: unknown): SystemConfigSnapshot {
  if (!isConfigSnapshot(value)) {
    throw new SystemConfigContractError();
  }

  try {
    return structuredClone(value);
  } catch {
    throw new SystemConfigContractError();
  }
}
