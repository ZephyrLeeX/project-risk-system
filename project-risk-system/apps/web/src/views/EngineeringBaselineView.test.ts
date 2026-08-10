import { describe, expect, it } from "vitest";

import {
  formatDateTime,
  formatWan,
  levelLabel,
  sourceLabel,
} from "@/utils/dashboard";

describe("dashboard presentation helpers", () => {
  it("formats yuan amounts as ten-thousand yuan without hiding missing data", () => {
    expect(formatWan("131775700")).toBe("13,177.57 万");
    expect(formatWan(null)).toBe("数据待补充");
  });

  it("maps risk labels consistently", () => {
    expect(levelLabel("HIGH")).toBe("高风险");
    expect(sourceLabel("LITIGATION")).toBe("发函诉讼清单");
  });

  it("keeps empty timestamps explicit", () => {
    expect(formatDateTime(null)).toBe("暂无更新时间");
  });
});
