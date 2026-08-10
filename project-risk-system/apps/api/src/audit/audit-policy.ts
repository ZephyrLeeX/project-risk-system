import type {
  AuditActionGroup,
  AuditModuleKey,
} from "@risk-platform/contracts";

const SENSITIVE_KEYS = new Set([
  "password",
  "passwd",
  "pwd",
  "oldpassword",
  "newpassword",
  "currentpassword",
  "confirmpassword",
  "token",
  "accesstoken",
  "refreshtoken",
  "idtoken",
  "sessiontoken",
  "sessionid",
  "secret",
  "clientsecret",
  "apikey",
  "accesskey",
  "secretkey",
  "authorization",
  "authcode",
  "credential",
  "credentials",
  "cookie",
  "cookies",
  "mailbody",
  "emailbody",
  "prompt",
  "rawresponse",
]);

function isSensitiveSnapshotKey(key: string): boolean {
  const normalized = key.replace(/[^a-z0-9]/gi, "").toLocaleLowerCase();
  if (SENSITIVE_KEYS.has(normalized)) return true;
  return (
    normalized.endsWith("password") ||
    /^(access|refresh|session|auth|bearer)token$/.test(normalized)
  );
}

const ACTION_LABELS: Record<string, string> = {
  AUTH_LOGIN_FAILED: "登录失败",
  AUTH_LOGIN_SUCCESS: "登录成功",
  AUTH_LOGOUT: "退出登录",
  AUTH_PASSWORD_CHANGED: "修改密码",
  AUTH_PASSWORD_CHANGE_FAILED: "修改密码失败",
  USER_CREATED: "新增用户",
  USER_UPDATED: "更新用户",
  USER_STATUS_CHANGED: "变更用户状态",
  USER_PASSWORD_RESET: "重置用户密码",
  USER_UNLOCKED: "解除账号锁定",
  ROLE_CREATED: "新增角色",
  ROLE_UPDATED: "更新角色权限",
  ROLE_DELETED: "删除角色",
  PROJECT_IMPORT_PREVIEWED: "解析校验",
  PROJECT_IMPORT_CONFIRMED: "发布批次",
  PROJECT_IMPORT_ROLLED_BACK: "回滚批次",
  AI_PROVIDER_CREATED: "新增AI服务",
  AI_PROVIDER_UPDATED: "更新AI服务",
  AI_PROVIDER_KEY_ROTATED: "轮换API Key",
  AI_PROVIDER_TESTED: "连接测试",
  AI_PROVIDER_DRAFT_TESTED: "草稿连接测试",
  AI_PROVIDER_DEFAULT_CHANGED: "切换默认服务",
  AI_PROVIDER_STATUS_CHANGED: "变更服务状态",
  SYSTEM_CONFIG_PUBLISHED: "发布配置",
  SYSTEM_CONFIG_PUBLISH_FAILED: "发布配置失败",
  RISK_RESOLVED: "解除风险",
  RISK_REOPENED: "重新打开风险",
  ACTION_ITEM_UPDATED: "更新待办",
  AUDIT_LOG_EXPORTED: "导出审计日志",
  AUDIT_LOG_EXPORT_FAILED: "导出审计日志失败",
};

export function auditModuleKey(module: string): AuditModuleKey {
  if (module === "AUTH") return "AUTH";
  if (["ADMIN_USER", "ADMIN_ROLE"].includes(module)) return "PERMISSION";
  if (module.startsWith("MAIL")) return "MAILBOX";
  if (["AI", "ADMIN_AI"].includes(module)) return "AI";
  if (["RISK", "TODO"].includes(module)) return "RISK";
  if (module === "IMPORT") return "IMPORT";
  if (module === "SYSTEM_CONFIG") return "CONFIG";
  if (module === "AUDIT") return "AUDIT";
  return "OTHER";
}

export function auditModuleLabel(key: AuditModuleKey): string {
  return {
    ALL: "全部模块",
    AUTH: "登录与账号",
    PERMISSION: "用户与权限",
    MAILBOX: "邮箱同步",
    AI: "API Key",
    RISK: "风险管理",
    IMPORT: "Excel导入",
    CONFIG: "系统配置",
    AUDIT: "审计治理",
    OTHER: "其他模块",
  }[key];
}

export function auditActionGroup(action: string): AuditActionGroup {
  if (/ROLLBACK|ROLLED_BACK/i.test(action)) return "ROLLBACK";
  if (/EXPORT/i.test(action)) return "EXPORT";
  if (/TEST/i.test(action)) return "TEST";
  if (/LOGIN|LOGOUT|PASSWORD/i.test(action)) return "LOGIN";
  if (/PUBLISH|PUBLISHED|CONFIRM|CONFIRMED/i.test(action)) return "PUBLISH";
  if (/CREATE|CREATED|REPORT|STARTED/i.test(action)) return "CREATE";
  if (/UPDATE|UPDATED|CHANGED|STATUS|RESOLVED|REOPENED|MATCHED|UNMATCHED|UNLOCK/i.test(action)) {
    return "UPDATE";
  }
  return "OTHER";
}

export function auditActionGroupLabel(group: AuditActionGroup): string {
  return {
    ALL: "全部操作",
    CREATE: "新增",
    UPDATE: "修改",
    TEST: "测试",
    LOGIN: "登录与账号",
    PUBLISH: "发布",
    ROLLBACK: "回滚",
    EXPORT: "导出",
    OTHER: "其他操作",
  }[group];
}

export function auditActionLabel(action: string): string {
  return (
    ACTION_LABELS[action] ??
    action
      .toLocaleLowerCase()
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toLocaleUpperCase() + part.slice(1))
      .join(" ")
  );
}

export function isSensitiveAuditEvent(module: string, action: string): boolean {
  return (
    ["ADMIN_USER", "ADMIN_ROLE", "ADMIN_AI", "SYSTEM_CONFIG", "AUDIT"].includes(module) ||
    /(PASSWORD|KEY|PERMISSION|SCOPE|ROLLBACK|ROLLED_BACK|PUBLISH|EXPORT|STATUS)/i.test(action)
  );
}

function maskEmail(value: string): string {
  const [local, domain] = value.split("@");
  if (!local || !domain) return value;
  return `${local.slice(0, 2)}***@${domain}`;
}

function sanitizeValue(value: unknown, key = "", depth = 0): unknown {
  if (depth > 8) return "[层级过深，已省略]";
  if (isSensitiveSnapshotKey(key)) return "[已脱敏]";
  if (value === null || value === undefined) return value ?? null;
  if (typeof value === "string") {
    if (/email/i.test(key)) return maskEmail(value);
    return value.length > 500 ? `${value.slice(0, 500)}…` : value;
  }
  if (["number", "boolean"].includes(typeof value)) return value;
  if (Array.isArray(value)) {
    return value.slice(0, 50).map((item) => sanitizeValue(item, key, depth + 1));
  }
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .slice(0, 80)
        .map(([childKey, childValue]) => [
          childKey,
          sanitizeValue(childValue, childKey, depth + 1),
        ]),
    );
  }
  return String(value);
}

export function sanitizeAuditSnapshot(
  snapshot: unknown,
): Record<string, unknown> | null {
  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) return null;
  return sanitizeValue(snapshot) as Record<string, unknown>;
}

export function auditSnapshotSummary(snapshot: unknown): string {
  const sanitized = sanitizeAuditSnapshot(snapshot);
  if (!sanitized) return "无变更快照";
  const parts: string[] = [];
  const walk = (value: unknown, prefix: string, depth: number): void => {
    if (parts.length >= 12 || depth > 4) return;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      Object.entries(value as Record<string, unknown>).forEach(([key, child]) => {
        walk(child, prefix ? `${prefix}.${key}` : key, depth + 1);
      });
      return;
    }
    const rendered = Array.isArray(value)
      ? value.length === 0
        ? "0项"
        : value.every((item) => ["string", "number", "boolean"].includes(typeof item))
          ? value.join("、")
          : `共${value.length}项`
      : String(value ?? "空");
    parts.push(`${prefix}：${rendered.slice(0, 120)}`);
  };
  walk(sanitized, "", 0);
  return parts.length ? parts.join("；") : "无变更快照";
}

export function maskClientIp(value: string | null | undefined): string {
  if (!value) return "-";
  const normalized = value.replace(/^::ffff:/, "");
  const ipv4 = normalized.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (ipv4) return `${ipv4[1]}.${ipv4[2]}.*.*`;
  if (normalized === "::1") return "本机";
  const ipv6 = normalized.split(":").filter(Boolean);
  return ipv6.length ? `${ipv6.slice(0, 2).join(":")}:***` : "-";
}

export function clientLabel(userAgent: string | null | undefined): string {
  if (!userAgent) return "未知客户端";
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /Chrome\//.test(userAgent)
      ? "Chrome"
      : /Firefox\//.test(userAgent)
        ? "Firefox"
        : /Safari\//.test(userAgent)
          ? "Safari"
          : "浏览器";
  const os = /Windows/i.test(userAgent)
    ? "Windows"
    : /Mac OS|Macintosh/i.test(userAgent)
      ? "macOS"
      : /Android/i.test(userAgent)
        ? "Android"
        : /iPhone|iPad/i.test(userAgent)
          ? "iOS"
          : "未知系统";
  return `${browser} · ${os}`;
}
