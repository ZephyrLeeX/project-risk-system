import { describe, expect, it } from "vitest";

import { canAccessWhilePasswordChangeRequired } from "./auth-session.guard";

describe("password-change-required route policy", () => {
  it("allows only session recovery, password change and logout", () => {
    expect(
      canAccessWhilePasswordChangeRequired("/api/auth/session"),
    ).toBe(true);
    expect(
      canAccessWhilePasswordChangeRequired(
        "/api/auth/change-password?source=first-login",
      ),
    ).toBe(true);
    expect(
      canAccessWhilePasswordChangeRequired("/api/auth/logout/"),
    ).toBe(true);
  });

  it("blocks protected business and administration routes", () => {
    expect(
      canAccessWhilePasswordChangeRequired("/api/admin/users"),
    ).toBe(false);
    expect(
      canAccessWhilePasswordChangeRequired("/api/health"),
    ).toBe(false);
  });
});
