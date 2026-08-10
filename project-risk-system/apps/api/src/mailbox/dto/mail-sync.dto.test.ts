import "reflect-metadata";
import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it } from "vitest";

import { ListMailMessagesQueryDto } from "./mail-sync.dto";

describe("ListMailMessagesQueryDto", () => {
  it("does not interpret the literal false query string as true", async () => {
    const dto = plainToInstance(ListMailMessagesQueryDto, { withRisk: "false", page: "2", pageSize: "20" });
    expect(await validate(dto)).toHaveLength(0);
    expect(dto.withRisk).toBe(false);
    expect(dto.page).toBe(2);
    expect(dto.pageSize).toBe(20);
  });

  it("accepts the literal true query string", async () => {
    const dto = plainToInstance(ListMailMessagesQueryDto, { withRisk: "true" });
    expect(await validate(dto)).toHaveLength(0);
    expect(dto.withRisk).toBe(true);
  });
});
