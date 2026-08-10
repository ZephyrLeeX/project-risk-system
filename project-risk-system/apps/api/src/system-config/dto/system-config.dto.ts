import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsBoolean,
  IsHexColor,
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  IsUUID,
  Max,
  MaxLength,
  Min,
  MinLength,
  ValidateNested,
} from "class-validator";

const RISK_LEVELS = ["HIGH", "MEDIUM", "LOW"] as const;
const CONFIG_MODULES = ["ALL", "RISK", "MAIL", "ALIAS", "SECURITY", "NOTIFICATION"] as const;

class RiskCategoryDto {
  @IsUUID()
  @IsOptional()
  id?: string | null;

  @IsString()
  @MinLength(2)
  @MaxLength(64)
  code!: string;

  @IsString()
  @MinLength(2)
  @MaxLength(128)
  name!: string;

  @IsArray()
  @ArrayMaxSize(30)
  @IsString({ each: true })
  keywords!: string[];

  @IsHexColor()
  colorToken!: string;

  @IsString()
  @MaxLength(500)
  @IsOptional()
  description?: string | null;

  @IsIn(RISK_LEVELS)
  @IsOptional()
  defaultLevel?: (typeof RISK_LEVELS)[number] | null;

  @Type(() => Number)
  @IsInt()
  @Min(0)
  @Max(10_000)
  sortOrder!: number;

  @IsBoolean()
  isActive!: boolean;
}

class RiskLevelRuleDto {
  @IsIn(RISK_LEVELS)
  level!: (typeof RISK_LEVELS)[number];

  @IsString()
  @MinLength(2)
  @MaxLength(32)
  displayName!: string;

  @IsHexColor()
  colorToken!: string;

  @IsString()
  @MinLength(4)
  @MaxLength(500)
  criteria!: string;

  @IsArray()
  @ArrayMaxSize(30)
  @IsString({ each: true })
  keywords!: string[];

  @Type(() => Number)
  @IsInt()
  @Min(0)
  @Max(10_000)
  sortOrder!: number;

  @IsBoolean()
  isActive!: boolean;
}

class ProjectAliasDto {
  @IsUUID()
  @IsOptional()
  id?: string | null;

  @IsUUID()
  projectId!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(255)
  alias!: string;

  @IsString()
  @MaxLength(64)
  source!: string;

  @IsString()
  @MaxLength(500)
  @IsOptional()
  note?: string | null;

  @IsBoolean()
  isActive!: boolean;
}

class MailSettingsDto {
  @Type(() => Number)
  @IsInt()
  @IsIn([15, 30, 60, 120])
  syncIntervalMinutes!: number;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(365)
  initialSyncDays!: number;

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(30)
  @IsString({ each: true })
  subjectKeywords!: string[];

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(50)
  @IsString({ each: true })
  riskKeywords!: string[];
}

class SecuritySettingsDto {
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(24)
  sessionHours!: number;

  @Type(() => Number)
  @IsInt()
  @Min(10)
  @Max(240)
  idleTimeoutMinutes!: number;

  @Type(() => Number)
  @IsInt()
  @Min(3)
  @Max(10)
  loginMaxAttempts!: number;

  @Type(() => Number)
  @IsInt()
  @Min(5)
  @Max(120)
  loginLockMinutes!: number;

  @Type(() => Number)
  @IsInt()
  @Min(8)
  @Max(128)
  passwordMinLength!: number;
}

class NotificationSettingsDto {
  @IsBoolean()
  mailboxSyncFailure!: boolean;

  @IsBoolean()
  apiKeyExpiry!: boolean;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(180)
  apiKeyExpiryDays!: number;

  @IsBoolean()
  importFailure!: boolean;

  @IsBoolean()
  abnormalLogin!: boolean;
}

export class PublishSystemConfigDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(50)
  @ValidateNested({ each: true })
  @Type(() => RiskCategoryDto)
  categories!: RiskCategoryDto[];

  @IsArray()
  @ArrayMinSize(3)
  @ArrayMaxSize(3)
  @ValidateNested({ each: true })
  @Type(() => RiskLevelRuleDto)
  levels!: RiskLevelRuleDto[];

  @IsArray()
  @ArrayMaxSize(1_000)
  @ValidateNested({ each: true })
  @Type(() => ProjectAliasDto)
  aliases!: ProjectAliasDto[];

  @ValidateNested()
  @Type(() => MailSettingsDto)
  mail!: MailSettingsDto;

  @ValidateNested()
  @Type(() => SecuritySettingsDto)
  security!: SecuritySettingsDto;

  @ValidateNested()
  @Type(() => NotificationSettingsDto)
  notifications!: NotificationSettingsDto;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(10_000)
  changeCount!: number;

  @IsString()
  @MinLength(4)
  @MaxLength(500)
  changeSummary!: string;

  @IsIn(CONFIG_MODULES)
  module!: (typeof CONFIG_MODULES)[number];
}

export class ListSystemConfigReleasesDto {
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(100)
  @IsOptional()
  limit = 30;

  @IsIn(CONFIG_MODULES)
  @IsOptional()
  module?: (typeof CONFIG_MODULES)[number];
}
