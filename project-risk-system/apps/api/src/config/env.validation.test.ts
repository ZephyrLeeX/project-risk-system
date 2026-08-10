import { describe, expect, it } from "vitest";

import { validateEnvironment } from "./env.validation";

describe("environment validation", () => {
  it("normalizes numeric security settings", () => {
    const result = validateEnvironment({
      SESSION_TTL_HOURS: "12",
      LOGIN_MAX_ATTEMPTS: "5",
    });

    expect(result.SESSION_TTL_HOURS).toBe(12);
    expect(result.LOGIN_MAX_ATTEMPTS).toBe(5);
  });

  it("rejects production without a strong session secret", () => {
    expect(() =>
      validateEnvironment({
        NODE_ENV: "production",
        DATABASE_URL: "postgresql://example",
        SESSION_SECRET: "short",
      }),
    ).toThrow("SESSION_SECRET");
  });
});
