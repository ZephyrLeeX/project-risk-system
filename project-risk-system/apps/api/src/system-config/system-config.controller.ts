import {
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Post,
  Query,
  Req,
  UseGuards,
} from "@nestjs/common";
import { randomUUID } from "node:crypto";

import type {
  ApiResponse,
  ProjectOption,
  SystemConfigOverview,
  SystemConfigReleaseDetail,
  SystemConfigReleaseItem,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import {
  ListSystemConfigReleasesDto,
  PublishSystemConfigDto,
} from "./dto/system-config.dto";
import { SystemConfigService } from "./system-config.service";

@Controller("admin/system-config")
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("admin.config.manage")
export class SystemConfigController {
  constructor(private readonly systemConfig: SystemConfigService) {}

  @Get()
  async overview(): Promise<ApiResponse<SystemConfigOverview>> {
    return this.ok(await this.systemConfig.overview());
  }

  @Get("project-options")
  async projectOptions(): Promise<ApiResponse<ProjectOption[]>> {
    return this.ok(await this.systemConfig.projectOptions());
  }

  @Post("publish")
  async publish(
    @Body() dto: PublishSystemConfigDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<SystemConfigOverview>> {
    return this.ok(
      await this.systemConfig.publish(dto, this.context(request)),
      "系统配置已保存并发布",
    );
  }

  @Get("releases")
  async releases(
    @Query() query: ListSystemConfigReleasesDto,
  ): Promise<ApiResponse<SystemConfigReleaseItem[]>> {
    return this.ok(await this.systemConfig.releases(query));
  }

  @Get("releases/:id")
  async releaseDetail(
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<SystemConfigReleaseDetail>> {
    return this.ok(await this.systemConfig.releaseDetail(id));
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
