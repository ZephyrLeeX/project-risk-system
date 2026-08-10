import { Body, Controller, Get, Post, Put, Req, UseGuards } from "@nestjs/common";
import { randomUUID } from "node:crypto";

import type {
  ApiResponse,
  MailboxConnectionTestResult,
  MailboxOverview,
  MailSyncBatchItem,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import { MailboxConfigDto, SetMailboxStatusDto } from "./dto/mailbox.dto";
import { MailboxService } from "./mailbox.service";

@Controller("mailbox/me")
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("mailbox.manage_self")
export class MailboxController {
  constructor(private readonly mailbox: MailboxService) {}

  @Get()
  async overview(@Req() request: AuthenticatedRequest): Promise<ApiResponse<MailboxOverview>> {
    return this.ok(await this.mailbox.overview(request.auth));
  }

  @Put()
  async save(
    @Body() dto: MailboxConfigDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<MailboxOverview>> {
    return this.ok(await this.mailbox.save(dto, this.context(request)), "个人邮箱配置已安全保存");
  }

  @Post("test")
  async test(
    @Body() dto: MailboxConfigDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<MailboxConnectionTestResult>> {
    const result = await this.mailbox.test(dto, this.context(request));
    return this.ok(result, result.success ? "邮箱连接测试通过" : "邮箱连接测试失败");
  }

  @Post("status")
  async status(
    @Body() dto: SetMailboxStatusDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<MailboxOverview>> {
    return this.ok(
      await this.mailbox.setStatus(dto.enabled, this.context(request)),
      dto.enabled ? "邮箱已恢复" : "邮箱已停用",
    );
  }

  @Post("sync")
  @RequirePermissions("mailbox.manage_self", "mailbox.sync_self")
  async sync(@Req() request: AuthenticatedRequest): Promise<ApiResponse<MailSyncBatchItem>> {
    return this.ok(await this.mailbox.startSync(this.context(request)), "邮箱同步任务已进入队列");
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
