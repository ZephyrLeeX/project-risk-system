import { Transform } from "class-transformer";
import {
  IsDateString,
  IsEnum,
  IsOptional,
  IsString,
  MaxLength,
  ValidateIf,
} from "class-validator";
import { ActionItemStatus } from "@prisma/client";

export class ListTodosQueryDto {
  @IsString()
  @MaxLength(128)
  @IsOptional()
  owner?: string;

  @IsEnum(ActionItemStatus)
  @IsOptional()
  status?: ActionItemStatus;
}

export class UpdateTodoDto {
  @IsEnum(ActionItemStatus)
  @IsOptional()
  status?: ActionItemStatus;

  @Transform(({ value }) =>
    typeof value === "string" ? value.trim() : value,
  )
  @IsString()
  @MaxLength(128)
  @IsOptional()
  assigneeName?: string;

  @ValidateIf((_object, value) => value !== null)
  @IsDateString({ strict: true })
  @IsOptional()
  dueDate?: string | null;

  @ValidateIf((_object, value) => value !== null)
  @IsString()
  @MaxLength(2000)
  @IsOptional()
  completionNote?: string | null;
}
