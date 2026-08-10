import {
  ArrayUnique,
  IsArray,
  IsBoolean,
  IsEnum,
  IsOptional,
  IsString,
  Matches,
  MaxLength,
  MinLength,
} from "class-validator";
import { DataScopeType } from "@prisma/client";

export class CreateRoleDto {
  @IsString()
  @MinLength(2)
  @MaxLength(128)
  name!: string;

  @IsString()
  @Matches(/^[A-Z][A-Z0-9_]{2,63}$/, {
    message: "角色编码需以大写字母开头，仅可包含大写字母、数字和下划线",
  })
  code!: string;

  @IsString()
  @MaxLength(500)
  @IsOptional()
  description?: string | null;

  @IsBoolean()
  enabled!: boolean;

  @IsEnum(DataScopeType)
  defaultDataScope!: DataScopeType;

  @IsArray()
  @ArrayUnique()
  @IsString({ each: true })
  permissionCodes!: string[];
}

export class UpdateRoleDto {
  @IsString()
  @MinLength(2)
  @MaxLength(128)
  name!: string;

  @IsString()
  @MaxLength(500)
  @IsOptional()
  description?: string | null;

  @IsBoolean()
  enabled!: boolean;

  @IsEnum(DataScopeType)
  defaultDataScope!: DataScopeType;

  @IsArray()
  @ArrayUnique()
  @IsString({ each: true })
  permissionCodes!: string[];
}
