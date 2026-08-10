import { Transform, Type } from "class-transformer";
import {
  IsEnum,
  IsInt,
  IsOptional,
  IsString,
  IsUUID,
  Max,
  MaxLength,
  Min,
  MinLength,
} from "class-validator";
import { MailMessageStatus, ProjectRiskLevel } from "@prisma/client";

export class ListMailMessagesQueryDto {
  @IsString()
  @MaxLength(128)
  @IsOptional()
  keyword?: string;

  @IsEnum(MailMessageStatus)
  @IsOptional()
  status?: MailMessageStatus;

  @IsUUID()
  @IsOptional()
  batchId?: string;

  @Transform(({ value }) => value === true || value === "true")
  @IsOptional()
  withRisk?: boolean;

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

export class ListMailBatchesQueryDto {
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

export class UpdateMailRiskCandidateDto {
  @IsUUID()
  projectId!: string;

  @IsUUID()
  categoryId!: string;

  @IsEnum(ProjectRiskLevel)
  level!: ProjectRiskLevel;

  @IsString()
  @MinLength(4)
  @MaxLength(4000)
  description!: string;

  @IsString()
  @MinLength(2)
  @MaxLength(4000)
  evidence!: string;

  @IsString()
  @MinLength(2)
  @MaxLength(4000)
  suggestion!: string;
}
