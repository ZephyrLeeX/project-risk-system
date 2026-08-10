import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it } from "vitest";

import {
  ReopenRiskDto,
  ResolveRiskDto,
} from "./risk-lifecycle.dto";

describe("risk lifecycle DTO", () => {
  it("requires a meaningful resolution reason", async () => {
    const dto = plainToInstance(ResolveRiskDto, { reason: " 短 " });
    expect(await validate(dto)).not.toHaveLength(0);
  });

  it("trims valid resolution reasons", async () => {
    const dto = plainToInstance(ResolveRiskDto, {
      reason: "  已完成处置并取得书面确认。  ",
    });
    expect(await validate(dto)).toHaveLength(0);
    expect(dto.reason).toBe("已完成处置并取得书面确认。");
  });

  it("requires a meaningful reopen reason", async () => {
    const dto = plainToInstance(ReopenRiskDto, { reason: "重开" });
    expect(await validate(dto)).not.toHaveLength(0);
  });
});
