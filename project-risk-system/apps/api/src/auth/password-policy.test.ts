import { describe, expect, it } from "vitest";

import { getPasswordPolicyViolations } from "./password-policy";

describe("password policy", () => {
  it("accepts a strong password", () => {
    expect(
      getPasswordPolicyViolations("Risk@2026Strong", {
        minLength: 12,
        username: "admin",
      }),
    ).toEqual([]);
  });

  it("reports all missing password classes", () => {
    const result = getPasswordPolicyViolations("admin", {
      minLength: 12,
      username: "admin",
    });

    expect(result).toContain("密码长度至少为 12 位");
    expect(result).toContain("密码需包含大写字母");
    expect(result).toContain("密码需包含数字");
    expect(result).toContain("密码需包含特殊字符");
    expect(result).toContain("密码不能包含登录账号");
  });
});
