import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsBoolean,
  IsEmail,
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  Matches,
  Max,
  MaxLength,
  Min,
  MinLength,
} from "class-validator";

const PROVIDERS = ["QQ", "IMAP"] as const;
const ENCRYPTIONS = ["SSL", "STARTTLS"] as const;
const INITIAL_SYNC_WEEKS = [1, 4, 8, 12] as const;

export class MailboxConfigDto {
  @IsIn(PROVIDERS)
  provider!: (typeof PROVIDERS)[number];

  @IsEmail({}, { message: "请输入有效的邮箱地址" })
  @MaxLength(255)
  email!: string;

  @IsOptional()
  @IsString()
  @MinLength(6, { message: "邮箱授权码至少6个字符" })
  @MaxLength(255)
  authCode?: string;

  @IsString()
  @MaxLength(255)
  @Matches(/^(?!.*\s)[a-z0-9.-]+$/i, { message: "IMAP服务器地址格式不正确" })
  imapHost!: string;

  @Type(() => Number)
  @IsInt()
  @Min(1, { message: "端口范围应为1至65535" })
  @Max(65_535, { message: "端口范围应为1至65535" })
  imapPort!: number;

  @IsIn(ENCRYPTIONS)
  encryption!: (typeof ENCRYPTIONS)[number];

  @IsString()
  @MinLength(1)
  @MaxLength(255)
  @Matches(/^[^\u0000-\u001f\u007f]+$/, { message: "邮件文件夹名称格式不正确" })
  folder!: string;

  @IsArray()
  @ArrayMinSize(1, { message: "至少配置一个主题关键词" })
  @ArrayMaxSize(8, { message: "最多配置8个主题关键词" })
  @IsString({ each: true })
  @MinLength(1, { each: true })
  @MaxLength(20, { each: true })
  subjectKeywords!: string[];

  @IsOptional()
  @IsString()
  @MaxLength(255)
  senderRule?: string;

  @Type(() => Number)
  @IsIn(INITIAL_SYNC_WEEKS)
  initialSyncWeeks!: (typeof INITIAL_SYNC_WEEKS)[number];

  @IsBoolean()
  readAttachments!: boolean;

  @IsBoolean()
  aiExtractionEnabled!: boolean;
}

export class SetMailboxStatusDto {
  @IsBoolean()
  enabled!: boolean;
}
