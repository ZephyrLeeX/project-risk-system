import { describe, expect, it } from "vitest";

import { hasAllPermissions } from "./permission.guard";

describe("permission matching", () => {
  it("requires every declared permission", () => {
    expect(
      hasAllPermissions(
        ["admin.user.manage", "admin.scope.manage"],
        ["admin.user.manage", "admin.scope.manage"],
      ),
    ).toBe(true);
    expect(
      hasAllPermissions(
        ["admin.user.manage"],
        ["admin.user.manage", "admin.scope.manage"],
      ),
    ).toBe(false);
  });
});
