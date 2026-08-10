import {
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Query,
  Req,
  UseGuards,
} from "@nestjs/common";
import { randomUUID } from "node:crypto";

import type {
  AiCallLogDetail,
  AiCallLogListItem,
  AiConnectionTestResult,
  AiProviderListItem,
  AiProviderStrategyItem,
  AiProviderSummary,
  AiUsageOverview,
  ApiResponse,
  PaginatedResponse,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import { AiProvidersService } from "./ai-providers.service";
import {
  AiUsageQueryDto,
  CreateAiProviderDto,
  ListAiCallsQueryDto,
  ListAiProvidersQueryDto,
  RotateAiProviderKeyDto,
  SetAiProviderStatusDto,
  TestAiProviderDraftDto,
  UpdateAiProviderDto,
} from "./dto/ai-provider.dto";

@Controller("admin/ai-services")
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("admin.ai.manage")
export class AiProvidersController {
  constructor(private readonly providers: AiProvidersService) {}

  @Get("summary")
  async summary(): Promise<ApiResponse<AiProviderSummary>> {
    return this.ok(await this.providers.summary());
  }

  @Get("strategy")
  async strategy(): Promise<ApiResponse<AiProviderStrategyItem[]>> {
    return this.ok(await this.providers.strategy());
  }

  @Get("usage")
  async usage(@Query() query: AiUsageQueryDto): Promise<ApiResponse<AiUsageOverview>> {
    return this.ok(await this.providers.usage(query));
  }

  @Get("calls")
  async calls(
    @Query() query: ListAiCallsQueryDto,
  ): Promise<ApiResponse<PaginatedResponse<AiCallLogListItem>>> {
    return this.ok(await this.providers.calls(query));
  }

  @Get("calls/:id")
  async callDetail(
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<AiCallLogDetail>> {
    return this.ok(await this.providers.callDetail(id));
  }

  @Post("test-draft")
  async testDraft(
    @Body() dto: TestAiProviderDraftDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiConnectionTestResult>> {
    return this.ok(
      await this.providers.testDraft(dto, this.context(request)),
      "连接测试已完成",
    );
  }

  @Post("test-all")
  async testAll(
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiConnectionTestResult[]>> {
    return this.ok(
      await this.providers.testAll(this.context(request)),
      "批量连接测试已完成",
    );
  }

  @Get()
  async list(
    @Query() query: ListAiProvidersQueryDto,
  ): Promise<ApiResponse<AiProviderListItem[]>> {
    return this.ok(await this.providers.list(query));
  }

  @Post()
  async create(
    @Body() dto: CreateAiProviderDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiProviderListItem>> {
    return this.ok(
      await this.providers.create(dto, this.context(request)),
      "AI服务配置已创建",
    );
  }

  @Patch(":id")
  async update(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: UpdateAiProviderDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiProviderListItem>> {
    return this.ok(
      await this.providers.update(id, dto, this.context(request)),
      "AI服务配置已保存",
    );
  }

  @Post(":id/rotate-key")
  async rotateKey(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: RotateAiProviderKeyDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiProviderListItem>> {
    return this.ok(
      await this.providers.rotateKey(id, dto, this.context(request)),
      "API Key已安全轮换",
    );
  }

  @Post(":id/test")
  async test(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiConnectionTestResult>> {
    return this.ok(
      await this.providers.testProvider(id, this.context(request)),
      "连接测试已完成",
    );
  }

  @Post(":id/set-default")
  async setDefault(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiProviderListItem>> {
    return this.ok(
      await this.providers.setDefault(id, this.context(request)),
      "默认AI服务已切换",
    );
  }

  @Post(":id/status")
  async setStatus(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: SetAiProviderStatusDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AiProviderListItem>> {
    return this.ok(
      await this.providers.setStatus(id, dto, this.context(request)),
      dto.enabled ? "AI服务已启用" : "AI服务已停用",
    );
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
