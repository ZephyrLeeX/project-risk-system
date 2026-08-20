import { describe, expect, it } from "vitest";

import { DATA_SCOPE_TYPES, ROLE_CODES } from "./index.js";

describe("shared contracts", () => {
  it("keeps the four confirmed system roles", () => {
    expect(ROLE_CODES).toEqual([
      "SYSTEM_ADMIN",
      "RISK_ADMIN",
      "PROJECT_MANAGER",
      "VIEWER_AUDITOR",
    ]);
  });

  it("keeps the confirmed project data scopes", () => {
    expect(DATA_SCOPE_TYPES).toEqual([
      "ALL",
      "OWNED",
      "ASSIGNED",
      "OWNED_OR_ASSIGNED",
      "NONE",
    ]);
  });
});
