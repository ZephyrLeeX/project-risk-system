import { Type } from "class-transformer";
import {
  ArrayUnique,
  IsArray,
  IsBoolean,
  IsEmail,
  IsEnum,
  IsInt,
  IsOptional,
  IsString,
  IsUUID,
  Matches,
  Max,
  MaxLength,
  Min,
  MinLength,
  ValidateIf,
} from "class-validator";
import { DataScopeType, UserStatus } from "@prisma/client";

export class ListUsersQueryDto {
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

  @IsString()
  @MaxLength(128)
  @IsOptional()
  keyword?: string;

  @IsString()
  @MaxLength(64)
  @IsOptional()
  roleCode?: string;

  @IsEnum(UserStatus)
  @IsOptional()
  status?: UserStatus;

  @IsUUID()
  @IsOptional()
  departmentId?: string;
}

export class CreateUserDto {
  @IsString()
  @MinLength(2)
  @MaxLength(128)
  displayName!: string;

  @IsString()
  @Matches(/^[a-zA-Z][a-zA-Z0-9._-]{2,63}$/, {
    message: "登录账号需以字母开头，仅可包含字母、数字、点、下划线和连字符",
  })
  username!: string;

  @ValidateIf((_object, value) => value !== null && value !== undefined && value !== "")
  @IsEmail()
  @MaxLength(255)
  @IsOptional()
  email?: string | null;

  @IsUUID()
  departmentId!: string;

  @IsUUID()
  roleId!: string;

  @IsEnum(DataScopeType)
  dataScope!: DataScopeType;

  @IsArray()
  @ArrayUnique()
  @IsUUID("4", { each: true })
  projectIds!: string[];

  @IsBoolean()
  enabled!: boolean;
}

export class UpdateUserDto extends CreateUserDto {}

export class SetUserStatusDto {
  @IsEnum(UserStatus)
  status!: UserStatus;
}

export class SetProjectScopesDto {
  @IsEnum(DataScopeType)
  dataScope!: DataScopeType;

  @IsArray()
  @ArrayUnique()
  @IsUUID("4", { each: true })
  projectIds!: string[];
}
