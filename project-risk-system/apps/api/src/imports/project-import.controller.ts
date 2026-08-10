import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Param,
  ParseUUIDPipe,
  Post,
  Query,
  Req,
  Res,
  StreamableFile,
  UploadedFile,
  UseGuards,
  UseInterceptors,
} from "@nestjs/common";
import type { Response } from "express";
import { FileInterceptor } from "@nestjs/platform-express";
import { randomUUID } from "node:crypto";

import type {
  ApiResponse,
  PaginatedResponse,
  ProjectImportBatchDetail,
  ProjectImportBatchSummary,
  ProjectOption,
} from "@risk-platform/contracts";

import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import {
  ConfirmProjectImportDto,
  ListImportBatchesQueryDto,
  MatchSupplementalCollectionDto,
} from "./dto/project-import.dto";
import { ProjectImportService } from "./project-import.service";

@Controller("imports/project-list")
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("admin.import.manage")
export class ProjectImportController {
  constructor(private readonly imports: ProjectImportService) {}

  @Post("preview")
  @UseInterceptors(
    FileInterceptor("file", {
      limits: {
        files: 1,
        fileSize: 20 * 1024 * 1024,
      },
    }),
  )
  async preview(
    @UploadedFile() file: Express.Multer.File | undefined,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<ProjectImportBatchDetail>> {
    if (!file) {
      throw new BadRequestException("请选择项目清单 Excel 文件");
    }
    return this.ok(
      await this.imports.preview(file, this.context(request)),
      "Excel 预检完成",
    );
  }

  @Get("batches")
  async list(
    @Query() query: ListImportBatchesQueryDto,
  ): Promise<ApiResponse<PaginatedResponse<ProjectImportBatchSummary>>> {
    return this.ok(await this.imports.list(query));
  }

  @Get("batches/:id")
  async detail(
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<ProjectImportBatchDetail>> {
    return this.ok(await this.imports.detail(id));
  }

  @Get("batches/:id/source")
  async sourceFile(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Res({ passthrough: true }) response: Response,
  ): Promise<StreamableFile> {
    const file = await this.imports.sourceFile(id);
    response.setHeader(
      "content-type",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );
    response.setHeader(
      "content-disposition",
      `attachment; filename*=UTF-8''${encodeURIComponent(file.fileName)}`,
    );
    return new StreamableFile(file.buffer);
  }

  @Get("projects/options")
  async projectOptions(): Promise<ApiResponse<ProjectOption[]>> {
    return this.ok(await this.imports.projectOptions());
  }

  @Post("supplemental/:id/match")
  async matchSupplemental(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: MatchSupplementalCollectionDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<ProjectImportBatchDetail>> {
    return this.ok(
      await this.imports.matchSupplemental(
        id,
        dto,
        this.context(request),
      ),
      "补充回款记录已关联项目",
    );
  }

  @Delete("supplemental/:id/match")
  async unmatchSupplemental(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<ProjectImportBatchDetail>> {
    return this.ok(
      await this.imports.unmatchSupplemental(id, this.context(request)),
      "补充回款记录已解除关联",
    );
  }

  @Post("batches/:id/confirm")
  async confirm(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: ConfirmProjectImportDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<ProjectImportBatchDetail>> {
    return this.ok(
      await this.imports.confirm(id, dto, this.context(request)),
      "项目清单已确认入库",
    );
  }

  @Post("batches/:id/rollback")
  async rollback(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<ProjectImportBatchDetail>> {
    return this.ok(
      await this.imports.rollback(id, this.context(request)),
      "导入批次已回滚",
    );
  }

  private context(request: AuthenticatedRequest) {
    return {
      identity: request.auth,
      clientIp: request.ip || request.socket.remoteAddress,
      userAgent: request.get("user-agent"),
    };
  }

  private ok<T>(data: T, message = "success"): ApiResponse<T> {
    return {
      code: "OK",
      message,
      data,
      traceId: randomUUID(),
    };
  }
}
