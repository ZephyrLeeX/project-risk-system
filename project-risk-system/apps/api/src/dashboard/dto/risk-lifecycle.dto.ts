import { Transform } from "class-transformer";
import { IsString, MaxLength, MinLength } from "class-validator";

function trimValue(value: unknown): unknown {
  return typeof value === "string" ? value.trim() : value;
}

export class ResolveRiskDto {
  @Transform(({ value }) => trimValue(value))
  @IsString()
  @MinLength(5)
  @MaxLength(2000)
  reason!: string;
}

export class ReopenRiskDto {
  @Transform(({ value }) => trimValue(value))
  @IsString()
  @MinLength(5)
  @MaxLength(2000)
  reason!: string;
}
