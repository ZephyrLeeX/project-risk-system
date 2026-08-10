import { DataScopeType } from "@prisma/client";
import { describe, expect, it } from "vitest";

import { buildProjectScopeWhere } from "./data-scope.service";

describe("project data scopes", () => {
  it("uses owner and explicit grants for combined scope", () => {
    const result = buildProjectScopeWhere(
      "00000000-0000-0000-0000-000000000001",
      DataScopeType.OWNED_OR_ASSIGNED,
    );

    expect(result.OR).toHaveLength(2);
  });

  it("returns no project for NONE", () => {
    expect(
      buildProjectScopeWhere(
        "00000000-0000-0000-0000-000000000001",
        DataScopeType.NONE,
      ),
    ).toEqual({
      id: { equals: "00000000-0000-0000-0000-000000000000" },
    });
  });
});
