import { describe, expect, it } from "vitest";

import { currentWeekLabel } from "@/utils/calendar";

describe("currentWeekLabel", () => {
  it("uses the ISO week-year instead of a hard-coded week", () => {
    expect(currentWeekLabel(new Date(2026, 7, 18))).toBe("2026年第34周");
  });

  it("handles the first days that belong to the previous ISO week-year", () => {
    expect(currentWeekLabel(new Date(2021, 0, 1))).toBe("2020年第53周");
  });
});
