import { describe, expect, it } from "vitest";

import { HealthService } from "./health.service";

describe("HealthService", () => {
  it("returns the API identity and status", () => {
    const result = new HealthService().getHealth();

    expect(result.service).toBe("project-risk-api");
    expect(result.status).toBe("ok");
    expect(new Date(result.timestamp).toString()).not.toBe("Invalid Date");
  });
});
