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
  DepartmentCollectionDetail,
  DepartmentCollectionSummary,
  DashboardFocusItem,
  DashboardRiskDetail,
  DashboardRiskFilterOptions,
  DashboardRiskListResponse,
  DashboardSummary,
  ResolvedRiskListResponse,
  RiskCollectionDetail,
  RiskCollectionListResponse,
  RiskTimelineDetail,
  RiskTimelineListResponse,
} from "@risk-platform/contracts";

import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import { DashboardService } from "./dashboard.service";
import {
  ListRiskCollectionsQueryDto,
  ListResolvedRisksQueryDto,
  ListRiskTimelineQueryDto,
  ListRisksQueryDto,
} from "./dto/dashboard-query.dto";
import {
  ReopenRiskDto,
  ResolveRiskDto,
} from "./dto/risk-lifecycle.dto";
import { RiskLifecycleService } from "./risk-lifecycle.service";

@Controller()
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("dashboard.view")
export class DashboardController {
  constructor(
    private readonly dashboard: DashboardService,
    private readonly riskLifecycle: RiskLifecycleService,
  ) {}

  @Get("dashboard/summary")
  async summary(
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<DashboardSummary>> {
    return this.ok(await this.dashboard.summary(request.auth));
  }

  @Get("dashboard/focus")
  async focus(
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<DashboardFocusItem[]>> {
    return this.ok(await this.dashboard.focus(request.auth));
  }

  @Get("dashboard/departments/collections")
  async departmentCollections(
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<DepartmentCollectionSummary>> {
    return this.ok(
      await this.dashboard.departmentCollections(request.auth),
    );
  }

  @Get("dashboard/departments/:departmentKey/collections")
  async departmentCollectionDetail(
    @Req() request: AuthenticatedRequest,
    @Param("departmentKey") departmentKey: string,
  ): Promise<ApiResponse<DepartmentCollectionDetail>> {
    return this.ok(
      await this.dashboard.departmentCollectionDetail(
        request.auth,
        departmentKey,
      ),
    );
  }

  @Get("dashboard/collections")
  async riskCollections(
    @Req() request: AuthenticatedRequest,
    @Query() query: ListRiskCollectionsQueryDto,
  ): Promise<ApiResponse<RiskCollectionListResponse>> {
    return this.ok(
      await this.dashboard.riskCollections(request.auth, query),
    );
  }

  @Get("dashboard/collections/:projectId")
  async riskCollectionDetail(
    @Req() request: AuthenticatedRequest,
    @Param("projectId", new ParseUUIDPipe()) projectId: string,
  ): Promise<ApiResponse<RiskCollectionDetail>> {
    return this.ok(
      await this.dashboard.riskCollectionDetail(
        request.auth,
        projectId,
      ),
    );
  }

  @Get("dashboard/timeline")
  async riskTimeline(
    @Req() request: AuthenticatedRequest,
    @Query() query: ListRiskTimelineQueryDto,
  ): Promise<ApiResponse<RiskTimelineListResponse>> {
    return this.ok(
      await this.dashboard.riskTimeline(request.auth, query),
    );
  }

  @Get("dashboard/timeline/:id")
  async riskTimelineDetail(
    @Req() request: AuthenticatedRequest,
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<RiskTimelineDetail>> {
    return this.ok(
      await this.dashboard.riskTimelineDetail(request.auth, id),
    );
  }

  @Get("risks/options")
  async options(
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<DashboardRiskFilterOptions>> {
    return this.ok(await this.dashboard.filterOptions(request.auth));
  }

  @Get("risks")
  async risks(
    @Req() request: AuthenticatedRequest,
    @Query() query: ListRisksQueryDto,
  ): Promise<ApiResponse<DashboardRiskListResponse>> {
    return this.ok(await this.dashboard.list(request.auth, query));
  }

  @Get("risks/resolved")
  async resolvedRisks(
    @Req() request: AuthenticatedRequest,
    @Query() query: ListResolvedRisksQueryDto,
  ): Promise<ApiResponse<ResolvedRiskListResponse>> {
    return this.ok(
      await this.dashboard.resolvedRisks(request.auth, query),
    );
  }

  @Post("risks/:id/resolve")
  @RequirePermissions("risk.resolve")
  async resolveRisk(
    @Req() request: AuthenticatedRequest,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: ResolveRiskDto,
  ): Promise<ApiResponse<DashboardRiskDetail>> {
    await this.riskLifecycle.resolve(id, dto.reason, {
      identity: request.auth,
      clientIp: request.ip || request.socket.remoteAddress,
      userAgent: request.get("user-agent"),
    });
    return this.ok(
      await this.dashboard.detail(request.auth, id),
      "风险已解除",
    );
  }

  @Post("risks/:id/reopen")
  @RequirePermissions("risk.resolve")
  async reopenRisk(
    @Req() request: AuthenticatedRequest,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: ReopenRiskDto,
  ): Promise<ApiResponse<DashboardRiskDetail>> {
    await this.riskLifecycle.reopen(id, dto.reason, {
      identity: request.auth,
      clientIp: request.ip || request.socket.remoteAddress,
      userAgent: request.get("user-agent"),
    });
    return this.ok(
      await this.dashboard.detail(request.auth, id),
      "风险已重新打开",
    );
  }

  @Get("risks/:id")
  async riskDetail(
    @Req() request: AuthenticatedRequest,
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<DashboardRiskDetail>> {
    return this.ok(await this.dashboard.detail(request.auth, id));
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
