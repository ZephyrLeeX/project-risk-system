const INTEGER_SETTINGS = {
  API_PORT: { fallback: 3000, min: 1, max: 65_535 },
  SESSION_TTL_HOURS: { fallback: 8, min: 1, max: 168 },
  LOGIN_MAX_ATTEMPTS: { fallback: 5, min: 3, max: 20 },
  LOGIN_LOCK_MINUTES: { fallback: 30, min: 1, max: 1_440 },
  PASSWORD_MIN_LENGTH: { fallback: 12, min: 12, max: 128 },
} as const;

export function validateEnvironment(
  raw: Record<string, unknown>,
): Record<string, unknown> {
  const config = { ...raw };

  for (const [key, rule] of Object.entries(INTEGER_SETTINGS)) {
    const value = Number(raw[key] ?? rule.fallback);
    if (!Number.isInteger(value) || value < rule.min || value > rule.max) {
      throw new Error(
        `${key} 必须是 ${rule.min} 至 ${rule.max} 之间的整数`,
      );
    }
    config[key] = value;
  }

  const nodeEnvironment = String(raw.NODE_ENV ?? "development");
  config.NODE_ENV = nodeEnvironment;
  if (nodeEnvironment === "production") {
    const sessionSecret = String(raw.SESSION_SECRET ?? "");
    if (sessionSecret.length < 32) {
      throw new Error("生产环境 SESSION_SECRET 长度至少为 32 位");
    }
    if (!raw.DATABASE_URL) {
      throw new Error("生产环境必须配置 DATABASE_URL");
    }
  }

  return config;
}
