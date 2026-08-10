import {
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Post,
  Query,
  Req,
  Res,
  UseGuards,
} from "@nestjs/common";
import { randomUUID } from "node:crypto";
import type { Response } from "express";

import type {
  ApiResponse,
  AuditLogDetail,
  AuditLogIntegrity,
  AuditLogListItem,
  AuditLogOptions,
  AuditLogSummary,
  PaginatedResponse,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import { AuditService } from "./audit.service";
import {
  ExportAuditLogsDto,
  ListAuditLogsQueryDto,
} from "./dto/audit-log.dto";

@Controller("admin/audit-logs")
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("admin.audit.view")
export class AuditController {
  constructor(private readonly audit: AuditService) {}

  @Get("summary")
  async summary(@Req() request: AuthenticatedRequest): Promise<ApiResponse<AuditLogSummary>> {
    return this.ok(await this.audit.summary(request.auth));
  }

  @Get("options")
  async options(@Req() request: AuthenticatedRequest): Promise<ApiResponse<AuditLogOptions>> {
    return this.ok(await this.audit.options(request.auth));
  }

  @Get("integrity")
  async integrity(): Promise<ApiResponse<AuditLogIntegrity>> {
    return this.ok(await this.audit.integrity());
  }

  @Post("export")
  @RequirePermissions("admin.audit.view", "admin.audit.export")
  async export(
    @Body() dto: ExportAuditLogsDto,
    @Req() request: AuthenticatedRequest,
    @Res() response: Response,
  ): Promise<void> {
    const file = await this.audit.export(dto, this.context(request));
    response.setHeader("content-type", file.mimeType);
    response.setHeader("content-length", String(file.buffer.length));
    response.setHeader(
      "content-disposition",
      `attachment; filename*=UTF-8''${encodeURIComponent(file.fileName)}`,
    );
    response.setHeader("x-export-count", String(file.count));
    response.send(file.buffer);
  }

  @Get()
  async list(
    @Query() query: ListAuditLogsQueryDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<PaginatedResponse<AuditLogListItem>>> {
    return this.ok(await this.audit.list(request.auth, query));
  }

  @Get(":id")
  async detail(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AuditLogDetail>> {
    return this.ok(await this.audit.detail(request.auth, id));
  }

  private context(request: AuthenticatedRequest): AdminRequestContext {
    return {
      identity: request.auth,
      clientIp: request.ip || request.socket.remoteAddress,
      userAgent: request.get("user-agent"),
    };
  }

  private ok<T>(data: T, message = "success"): ApiResponse<T> {
    return { code: "OK", message, data, traceId: randomUUID() };
  }
}
