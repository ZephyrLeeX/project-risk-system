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
  ApiResponse,
  MailMessageDetail,
  MailMessageListResponse,
  MailRiskCandidateItem,
  MailRiskReviewOptions,
  MailSyncBatchDetail,
  MailSyncBatchItem,
  MailSyncSummary,
  PaginatedResponse,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import {
  ListMailBatchesQueryDto,
  ListMailMessagesQueryDto,
  UpdateMailRiskCandidateDto,
} from "./dto/mail-sync.dto";
import { MailSyncResultsService } from "./mail-sync-results.service";

@Controller("mailbox")
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("mailbox.sync_self")
export class MailSyncResultsController {
  constructor(private readonly results: MailSyncResultsService) {}

  @Get("sync-summary")
  async summary(@Req() request: AuthenticatedRequest): Promise<ApiResponse<MailSyncSummary>> {
    return this.ok(await this.results.summary(request.auth));
  }

  @Get("review-options")
  async reviewOptions(@Req() request: AuthenticatedRequest): Promise<ApiResponse<MailRiskReviewOptions>> {
    return this.ok(await this.results.reviewOptions(request.auth));
  }

  @Get("messages")
  async messages(@Query() query: ListMailMessagesQueryDto, @Req() request: AuthenticatedRequest): Promise<ApiResponse<MailMessageListResponse>> {
    return this.ok(await this.results.messages(request.auth, query));
  }

  @Get("messages/:id")
  async message(@Param("id", ParseUUIDPipe) id: string, @Req() request: AuthenticatedRequest): Promise<ApiResponse<MailMessageDetail>> {
    return this.ok(await this.results.message(request.auth, id));
  }

  @Post("messages/:id/retry")
  async retry(@Param("id", ParseUUIDPipe) id: string, @Req() request: AuthenticatedRequest): Promise<ApiResponse<MailSyncBatchItem>> {
    return this.ok(await this.results.retry(id, this.context(request)), "失败邮件已进入重新处理队列");
  }

  @Get("sync-batches")
  async batches(@Query() query: ListMailBatchesQueryDto, @Req() request: AuthenticatedRequest): Promise<ApiResponse<PaginatedResponse<MailSyncBatchItem>>> {
    return this.ok(await this.results.batches(request.auth, query));
  }

  @Get("sync-batches/:id")
  async batch(@Param("id", ParseUUIDPipe) id: string, @Req() request: AuthenticatedRequest): Promise<ApiResponse<MailSyncBatchDetail>> {
    return this.ok(await this.results.batch(request.auth, id));
  }

  @Patch("risk-candidates/:id")
  @RequirePermissions("mailbox.sync_self", "risk.resolve")
  async updateCandidate(@Param("id", ParseUUIDPipe) id: string, @Body() dto: UpdateMailRiskCandidateDto, @Req() request: AuthenticatedRequest): Promise<ApiResponse<MailRiskCandidateItem>> {
    return this.ok(await this.results.updateCandidate(id, dto, this.context(request)), "风险线索已调整");
  }

  @Post("risk-candidates/:id/ignore")
  @RequirePermissions("mailbox.sync_self", "risk.resolve")
  async ignoreCandidate(@Param("id", ParseUUIDPipe) id: string, @Req() request: AuthenticatedRequest): Promise<ApiResponse<MailRiskCandidateItem>> {
    return this.ok(await this.results.ignoreCandidate(id, this.context(request)), "风险线索已忽略");
  }

  @Post("risk-candidates/:id/confirm")
  @RequirePermissions("mailbox.sync_self", "risk.resolve")
  async confirmCandidate(@Param("id", ParseUUIDPipe) id: string, @Req() request: AuthenticatedRequest): Promise<ApiResponse<MailRiskCandidateItem>> {
    return this.ok(await this.results.confirmCandidate(id, this.context(request)), "风险线索已确认并发布到看板");
  }

  private context(request: AuthenticatedRequest): AdminRequestContext {
    return { identity: request.auth, clientIp: request.ip || request.socket.remoteAddress, userAgent: request.get("user-agent") };
  }

  private ok<T>(data: T, message = "success"): ApiResponse<T> {
    return { code: "OK", message, data, traceId: randomUUID() };
  }
}
