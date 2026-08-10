import { BadRequestException } from "@nestjs/common";
import { DataScopeType } from "@prisma/client";
import { describe, expect, it } from "vitest";

import { validateRolePolicy } from "./role-policy";

describe("role security policy", () => {
  it("prevents mailbox permission from leaking to other roles", () => {
    expect(() =>
      validateRolePolicy(
        "PROJECT_MANAGER",
        ["dashboard.view", "mailbox.manage_self"],
        DataScopeType.OWNED,
      ),
    ).toThrow(BadRequestException);
  });

  it("accepts the confirmed project manager boundary", () => {
    expect(() =>
      validateRolePolicy(
        "PROJECT_MANAGER",
        ["dashboard.view", "agent.use", "risk.report", "risk.resolve"],
        DataScopeType.OWNED_OR_ASSIGNED,
      ),
    ).not.toThrow();
  });
});
