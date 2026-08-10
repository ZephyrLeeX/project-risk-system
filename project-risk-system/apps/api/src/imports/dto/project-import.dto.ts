import { Type } from "class-transformer";
import {
  IsBoolean,
  IsInt,
  IsOptional,
  IsUUID,
  Max,
  Min,
} from "class-validator";

export class ListImportBatchesQueryDto {
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @IsOptional()
  page = 1;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(50)
  @IsOptional()
  pageSize = 10;
}

export class ConfirmProjectImportDto {
  @IsBoolean()
  acknowledgeWarnings!: boolean;
}

export class MatchSupplementalCollectionDto {
  @IsUUID()
  projectId!: string;
}
