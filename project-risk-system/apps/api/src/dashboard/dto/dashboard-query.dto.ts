import { Type } from "class-transformer";
import {
  IsEnum,
  IsInt,
  IsOptional,
  IsString,
  IsUUID,
  Max,
  MaxLength,
  Min,
} from "class-validator";
import {
  ProjectRiskLevel,
  RiskSourceType,
  RiskTimelineEventType,
} from "@prisma/client";

export class ListRisksQueryDto {
  @IsString()
  @MaxLength(100)
  @IsOptional()
  keyword?: string;

  @IsEnum(ProjectRiskLevel)
  @IsOptional()
  level?: ProjectRiskLevel;

  @IsUUID()
  @IsOptional()
  categoryId?: string;

  @IsString()
  @MaxLength(100)
  @IsOptional()
  owner?: string;

  @IsEnum(RiskSourceType)
  @IsOptional()
  sourceType?: RiskSourceType;

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
  pageSize = 20;
}

export class ListRiskCollectionsQueryDto {
  @IsString()
  @MaxLength(100)
  @IsOptional()
  keyword?: string;

  @IsEnum(ProjectRiskLevel)
  @IsOptional()
  level?: ProjectRiskLevel;

  @IsString()
  @MaxLength(100)
  @IsOptional()
  owner?: string;
}

export class ListRiskTimelineQueryDto {
  @IsString()
  @MaxLength(100)
  @IsOptional()
  keyword?: string;

  @IsEnum(ProjectRiskLevel)
  @IsOptional()
  level?: ProjectRiskLevel;

  @IsEnum(RiskTimelineEventType)
  @IsOptional()
  eventType?: RiskTimelineEventType;

  @IsUUID()
  @IsOptional()
  projectId?: string;

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
  pageSize = 20;
}

export class ListResolvedRisksQueryDto extends ListRisksQueryDto {}
