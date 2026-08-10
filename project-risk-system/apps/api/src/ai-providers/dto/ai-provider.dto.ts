import { Type } from "class-transformer";
import {
  IsBoolean,
  IsDateString,
  IsEnum,
  IsInt,
  IsIn,
  IsOptional,
  IsString,
  IsUrl,
  Max,
  MaxLength,
  Min,
  MinLength,
  ValidateIf,
} from "class-validator";
import { AiCallResult, AiCallScene } from "@prisma/client";

export class ListAiProvidersQueryDto {
  @IsString()
  @MaxLength(128)
  @IsOptional()
  keyword?: string;

  @IsIn(["ACTIVE", "DISABLED"])
  @IsOptional()
  status?: "ACTIVE" | "DISABLED";
}

export class CreateAiProviderDto {
  @IsString()
  @MinLength(2)
  @MaxLength(128)
  name!: string;

  @IsString()
  @MinLength(2)
  @MaxLength(128)
  vendor!: string;

  @IsUrl({ protocols: ["https"], require_protocol: true })
  @MaxLength(500)
  endpoint!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  model!: string;

  @IsString()
  @MinLength(8)
  @MaxLength(500)
  apiKey!: string;

  @ValidateIf((_object, value) => value !== null && value !== "")
  @IsDateString()
  @IsOptional()
  expiresAt?: string | null;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(300)
  timeoutSeconds!: number;

  @Type(() => Number)
  @IsInt()
  @Min(0)
  @Max(5)
  retryCount!: number;

  @IsBoolean()
  enabled!: boolean;
}

export class UpdateAiProviderDto {
  @IsString()
  @MinLength(2)
  @MaxLength(128)
  name!: string;

  @IsString()
  @MinLength(2)
  @MaxLength(128)
  vendor!: string;

  @IsUrl({ protocols: ["https"], require_protocol: true })
  @MaxLength(500)
  endpoint!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  model!: string;

  @ValidateIf((_object, value) => value !== null && value !== "")
  @IsDateString()
  @IsOptional()
  expiresAt?: string | null;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(300)
  timeoutSeconds!: number;

  @Type(() => Number)
  @IsInt()
  @Min(0)
  @Max(5)
  retryCount!: number;

  @IsBoolean()
  enabled!: boolean;
}

export class RotateAiProviderKeyDto {
  @IsString()
  @MinLength(8)
  @MaxLength(500)
  apiKey!: string;

  @ValidateIf((_object, value) => value !== null && value !== "")
  @IsDateString()
  @IsOptional()
  expiresAt?: string | null;
}

export class SetAiProviderStatusDto {
  @IsBoolean()
  enabled!: boolean;
}

export class TestAiProviderDraftDto {
  @IsString()
  @MinLength(2)
  @MaxLength(128)
  name!: string;

  @IsUrl({ protocols: ["https"], require_protocol: true })
  @MaxLength(500)
  endpoint!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  model!: string;

  @IsString()
  @MinLength(8)
  @MaxLength(500)
  apiKey!: string;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(300)
  timeoutSeconds!: number;

  @Type(() => Number)
  @IsInt()
  @Min(0)
  @Max(5)
  retryCount!: number;
}

export class AiUsageQueryDto {
  @IsEnum(AiCallScene)
  @IsOptional()
  scene?: AiCallScene;
}

export class ListAiCallsQueryDto {
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @IsOptional()
  page = 1;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(100)
  @IsOptional()
  pageSize = 10;

  @IsEnum(AiCallResult)
  @IsOptional()
  result?: AiCallResult;

  @IsEnum(AiCallScene)
  @IsOptional()
  scene?: AiCallScene;
}
