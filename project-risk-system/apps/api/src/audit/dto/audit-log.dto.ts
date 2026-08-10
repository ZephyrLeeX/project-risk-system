import { Transform, Type } from "class-transformer";
import {
  IsBoolean,
  IsDateString,
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  MinLength,
} from "class-validator";

const MODULES = ["ALL", "AUTH", "PERMISSION", "MAILBOX", "AI", "RISK", "IMPORT", "CONFIG", "AUDIT", "OTHER"] as const;
const ACTIONS = ["ALL", "CREATE", "UPDATE", "TEST", "LOGIN", "PUBLISH", "ROLLBACK", "EXPORT", "OTHER"] as const;
const DATE_RANGES = ["TODAY", "7_DAYS", "30_DAYS", "CUSTOM"] as const;

export class AuditFilterDto {
  @IsString()
  @MaxLength(128)
  @IsOptional()
  keyword?: string;

  @IsIn(MODULES)
  @IsOptional()
  module: (typeof MODULES)[number] = "ALL";

  @IsIn(ACTIONS)
  @IsOptional()
  action: (typeof ACTIONS)[number] = "ALL";

  @IsIn(["SUCCESS", "FAILURE"])
  @IsOptional()
  result?: "SUCCESS" | "FAILURE";

  @IsIn(DATE_RANGES)
  @IsOptional()
  dateRange: (typeof DATE_RANGES)[number] = "TODAY";

  @IsDateString()
  @IsOptional()
  startDate?: string;

  @IsDateString()
  @IsOptional()
  endDate?: string;

  @Transform(({ value }) => value === true || value === "true")
  @IsBoolean()
  @IsOptional()
  sensitiveOnly = false;
}

export class ListAuditLogsQueryDto extends AuditFilterDto {
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
}

export class ExportAuditLogsDto extends AuditFilterDto {
  @IsIn(["XLSX", "CSV"])
  format!: "XLSX" | "CSV";

  @IsString()
  @MinLength(4)
  @MaxLength(200)
  reason!: string;
}
