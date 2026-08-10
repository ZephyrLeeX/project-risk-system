import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  AiCallResult,
  AiCallScene,
  AiConnectionStatus,
  AuditResult,
  type AiProviderConfig,
  type Prisma,
} from "@prisma/client";
import { randomUUID } from "node:crypto";

import type {
  AiCallLogDetail,
  AiCallLogListItem,
  AiConnectionTestResult,
  AiProviderListItem,
  AiProviderStrategyItem,
  AiProviderSummary,
  AiUsageOverview,
  PaginatedResponse,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuditService } from "../audit/audit.service";
import { PrismaService } from "../prisma/prisma.service";
import { AiConnectionService } from "./ai-connection.service";
import { CredentialEncryptionService } from "./credential-encryption.service";
import type {
  AiUsageQueryDto,
  CreateAiProviderDto,
  ListAiCallsQueryDto,
  ListAiProvidersQueryDto,
  RotateAiProviderKeyDto,
  SetAiProviderStatusDto,
  TestAiProviderDraftDto,
  UpdateAiProviderDto,
} from "./dto/ai-provider.dto";

const DATA_PROTECTION_NOTICE =
  "不保存API Key，不默认保存完整邮件、提示词和模型原始响应。";

@Injectable()
export class AiProvidersService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly encryption: CredentialEncryptionService,
    private readonly connection: AiConnectionService,
    private readonly audit: AuditService,
  ) {}

  async summary(): Promise<AiProviderSummary> {
    const now = new Date();
    const expiresBefore = new Date(now.getTime() + 30 * 86_400_000);
    const sevenDaysAgo = new Date(now.getTime() - 7 * 86_400_000);
    const [total, healthy, expiring, callTotal, successTotal] =
      await Promise.all([
        this.prisma.aiProviderConfig.count(),
        this.prisma.aiProviderConfig.count({
          where: { enabled: true, lastTestStatus: AiConnectionStatus.HEALTHY },
        }),
        this.prisma.aiProviderConfig.count({
          where: {
            enabled: true,
            expiresAt: { gte: now, lte: expiresBefore },
          },
        }),
        this.prisma.aiCallLog.count({ where: { createdAt: { gte: sevenDaysAgo } } }),
        this.prisma.aiCallLog.count({
          where: {
            createdAt: { gte: sevenDaysAgo },
            result: AiCallResult.SUCCESS,
          },
        }),
      ]);
    return {
      total,
      healthy,
      expiring,
      sevenDayCallTotal: callTotal,
      sevenDaySuccessRate: callTotal
        ? this.round((successTotal / callTotal) * 100)
        : 0,
    };
  }

  async list(query: ListAiProvidersQueryDto): Promise<AiProviderListItem[]> {
    const sevenDaysAgo = new Date(Date.now() - 7 * 86_400_000);
    const where: Prisma.AiProviderConfigWhereInput = {
      ...(query.status === "ACTIVE"
        ? { enabled: true }
        : query.status === "DISABLED"
          ? { enabled: false }
          : {}),
      ...(query.keyword?.trim()
        ? {
            OR: ["name", "vendor", "model", "endpoint"].map((field) => ({
              [field]: {
                contains: query.keyword!.trim(),
                mode: "insensitive" as const,
              },
            })),
          }
        : {}),
    };
    const [providers, usage] = await Promise.all([
      this.prisma.aiProviderConfig.findMany({
        where,
        orderBy: [{ isDefault: "desc" }, { priority: "asc" }, { createdAt: "asc" }],
      }),
      this.prisma.aiCallLog.groupBy({
        by: ["providerId"],
        where: { createdAt: { gte: sevenDaysAgo }, providerId: { not: null } },
        _count: { _all: true },
      }),
    ]);
    const usageByProvider = new Map(
      usage.map((item) => [item.providerId, item._count._all]),
    );
    return providers.map((provider) =>
      this.mapProvider(provider, usageByProvider.get(provider.id) ?? 0),
    );
  }

  async strategy(): Promise<AiProviderStrategyItem[]> {
    const providers = await this.prisma.aiProviderConfig.findMany({
      orderBy: [{ isDefault: "desc" }, { priority: "asc" }, { createdAt: "asc" }],
    });
    return providers.map((provider) => ({
      id: provider.id,
      name: provider.name,
      enabled: provider.enabled,
      isDefault: provider.isDefault,
      priority: provider.priority,
    }));
  }

  async create(
    dto: CreateAiProviderDto,
    context: AdminRequestContext,
  ): Promise<AiProviderListItem> {
    await this.ensureNameAvailable(dto.name);
    const credential = this.encryption.encrypt(dto.apiKey);
    const provider = await this.prisma.$transaction(async (transaction) => {
      const existingCount = await transaction.aiProviderConfig.count();
      return transaction.aiProviderConfig.create({
        data: {
          name: dto.name.trim(),
          vendor: dto.vendor.trim(),
          endpoint: this.normalizeEndpoint(dto.endpoint),
          model: dto.model.trim(),
          encryptedApiKey: credential.ciphertext,
          keyIv: credential.iv,
          keyAuthTag: credential.authTag,
          keyLast4: credential.last4,
          expiresAt: this.toDate(dto.expiresAt),
          timeoutSeconds: dto.timeoutSeconds,
          retryCount: dto.retryCount,
          enabled: dto.enabled,
          isDefault: existingCount === 0 && dto.enabled,
          priority: (existingCount + 1) * 100,
          createdById: context.identity.user.id,
          updatedById: context.identity.user.id,
        },
      });
    });
    await this.recordAudit("AI_PROVIDER_CREATED", provider, context);
    return this.mapProvider(provider, 0);
  }

  async update(
    id: string,
    dto: UpdateAiProviderDto,
    context: AdminRequestContext,
  ): Promise<AiProviderListItem> {
    const existing = await this.getProvider(id);
    await this.ensureNameAvailable(dto.name, id);
    if (existing.isDefault && !dto.enabled) {
      throw new BadRequestException("默认服务不能直接停用，请先切换默认服务");
    }
    const provider = await this.prisma.aiProviderConfig.update({
      where: { id },
      data: {
        name: dto.name.trim(),
        vendor: dto.vendor.trim(),
        endpoint: this.normalizeEndpoint(dto.endpoint),
        model: dto.model.trim(),
        expiresAt: this.toDate(dto.expiresAt),
        timeoutSeconds: dto.timeoutSeconds,
        retryCount: dto.retryCount,
        enabled: dto.enabled,
        updatedById: context.identity.user.id,
      },
    });
    await this.recordAudit("AI_PROVIDER_UPDATED", provider, context, existing);
    return this.mapProvider(provider, await this.usageCount(id));
  }

  async rotateKey(
    id: string,
    dto: RotateAiProviderKeyDto,
    context: AdminRequestContext,
  ): Promise<AiProviderListItem> {
    const existing = await this.getProvider(id);
    const credential = this.encryption.encrypt(dto.apiKey);
    const provider = await this.prisma.aiProviderConfig.update({
      where: { id },
      data: {
        encryptedApiKey: credential.ciphertext,
        keyIv: credential.iv,
        keyAuthTag: credential.authTag,
        keyLast4: credential.last4,
        expiresAt:
          dto.expiresAt === undefined
            ? existing.expiresAt
            : this.toDate(dto.expiresAt),
        lastTestStatus: AiConnectionStatus.UNTESTED,
        lastTestAt: null,
        lastTestLatencyMs: null,
        lastTestErrorCode: null,
        updatedById: context.identity.user.id,
      },
    });
    await this.recordAudit("AI_PROVIDER_KEY_ROTATED", provider, context, existing);
    return this.mapProvider(provider, await this.usageCount(id));
  }

  async setStatus(
    id: string,
    dto: SetAiProviderStatusDto,
    context: AdminRequestContext,
  ): Promise<AiProviderListItem> {
    const existing = await this.getProvider(id);
    if (existing.isDefault && !dto.enabled) {
      throw new BadRequestException("默认服务不能直接停用，请先切换默认服务");
    }
    const provider = await this.prisma.aiProviderConfig.update({
      where: { id },
      data: { enabled: dto.enabled, updatedById: context.identity.user.id },
    });
    await this.recordAudit("AI_PROVIDER_STATUS_CHANGED", provider, context, existing);
    return this.mapProvider(provider, await this.usageCount(id));
  }

  async setDefault(
    id: string,
    context: AdminRequestContext,
  ): Promise<AiProviderListItem> {
    const existing = await this.getProvider(id);
    if (!existing.enabled) {
      throw new BadRequestException("停用的AI服务不能设为默认服务");
    }
    const previous = await this.prisma.aiProviderConfig.findFirst({
      where: { isDefault: true },
    });
    const provider = await this.prisma.$transaction(async (transaction) => {
      await transaction.aiProviderConfig.updateMany({
        where: { isDefault: true },
        data: { isDefault: false, updatedById: context.identity.user.id },
      });
      return transaction.aiProviderConfig.update({
        where: { id },
        data: { isDefault: true, updatedById: context.identity.user.id },
      });
    });
    await this.recordAudit(
      "AI_PROVIDER_DEFAULT_CHANGED",
      provider,
      context,
      previous ?? undefined,
    );
    return this.mapProvider(provider, await this.usageCount(id));
  }

  async testProvider(
    id: string,
    context: AdminRequestContext,
  ): Promise<AiConnectionTestResult> {
    const provider = await this.getProvider(id);
    const apiKey = this.encryption.decrypt(provider);
    return this.runTest(
      {
        providerId: provider.id,
        providerName: provider.name,
        endpoint: provider.endpoint,
        model: provider.model,
        apiKey,
        timeoutSeconds: provider.timeoutSeconds,
        retryCount: provider.retryCount,
      },
      context,
    );
  }

  async testDraft(
    dto: TestAiProviderDraftDto,
    context: AdminRequestContext,
  ): Promise<AiConnectionTestResult> {
    return this.runTest(
      {
        providerId: null,
        providerName: dto.name.trim(),
        endpoint: dto.endpoint,
        model: dto.model.trim(),
        apiKey: dto.apiKey,
        timeoutSeconds: dto.timeoutSeconds,
        retryCount: dto.retryCount,
      },
      context,
    );
  }

  async testAll(context: AdminRequestContext): Promise<AiConnectionTestResult[]> {
    const providers = await this.prisma.aiProviderConfig.findMany({
      where: { enabled: true },
      orderBy: [{ isDefault: "desc" }, { priority: "asc" }],
    });
    const results: AiConnectionTestResult[] = [];
    for (const provider of providers) {
      results.push(await this.testProvider(provider.id, context));
    }
    return results;
  }

  async usage(query: AiUsageQueryDto): Promise<AiUsageOverview> {
    const rangeEnd = new Date();
    const rangeStart = new Date(rangeEnd.getTime() - 7 * 86_400_000);
    const calls = await this.prisma.aiCallLog.findMany({
      where: {
        createdAt: { gte: rangeStart, lte: rangeEnd },
        ...(query.scene ? { scene: query.scene } : {}),
      },
      select: {
        createdAt: true,
        durationMs: true,
        totalTokens: true,
        result: true,
      },
      orderBy: { createdAt: "asc" },
    });
    const successTotal = calls.filter(
      (item) => item.result === AiCallResult.SUCCESS,
    ).length;
    const durations = calls.map((item) => item.durationMs).sort((a, b) => a - b);
    const trendMap = new Map<string, number>();
    for (let offset = 6; offset >= 0; offset -= 1) {
      const date = new Date(rangeEnd.getTime() - offset * 86_400_000);
      trendMap.set(date.toISOString().slice(0, 10), 0);
    }
    calls.forEach((item) => {
      const key = item.createdAt.toISOString().slice(0, 10);
      trendMap.set(key, (trendMap.get(key) ?? 0) + 1);
    });
    return {
      rangeStart: rangeStart.toISOString(),
      rangeEnd: rangeEnd.toISOString(),
      callTotal: calls.length,
      successTotal,
      successRate: calls.length
        ? this.round((successTotal / calls.length) * 100)
        : 0,
      averageDurationMs: calls.length
        ? Math.round(calls.reduce((sum, item) => sum + item.durationMs, 0) / calls.length)
        : 0,
      p95DurationMs: durations.length
        ? durations[Math.min(durations.length - 1, Math.ceil(durations.length * 0.95) - 1)]!
        : 0,
      totalTokens: calls.reduce((sum, item) => sum + item.totalTokens, 0),
      trend: [...trendMap.entries()].map(([date, count]) => ({ date, count })),
    };
  }

  async calls(
    query: ListAiCallsQueryDto,
  ): Promise<PaginatedResponse<AiCallLogListItem>> {
    const where: Prisma.AiCallLogWhereInput = {
      ...(query.result ? { result: query.result } : {}),
      ...(query.scene ? { scene: query.scene } : {}),
    };
    const [items, total] = await Promise.all([
      this.prisma.aiCallLog.findMany({
        where,
        orderBy: { createdAt: "desc" },
        skip: (query.page - 1) * query.pageSize,
        take: query.pageSize,
      }),
      this.prisma.aiCallLog.count({ where }),
    ]);
    return {
      items: items.map((item) => this.mapCall(item)),
      total,
      page: query.page,
      pageSize: query.pageSize,
    };
  }

  async callDetail(id: string): Promise<AiCallLogDetail> {
    const item = await this.prisma.aiCallLog.findUnique({
      where: { id },
      include: { actor: { select: { displayName: true } } },
    });
    if (!item) throw new NotFoundException("AI调用记录不存在");
    return {
      ...this.mapCall(item),
      inputTokens: item.inputTokens,
      outputTokens: item.outputTokens,
      actorDisplayName: item.actor?.displayName ?? null,
      dataProtectionNotice: DATA_PROTECTION_NOTICE,
    };
  }

  private async runTest(
    request: {
      providerId: string | null;
      providerName: string;
      endpoint: string;
      model: string;
      apiKey: string;
      timeoutSeconds: number;
      retryCount: number;
    },
    context: AdminRequestContext,
  ): Promise<AiConnectionTestResult> {
    const testedAt = new Date();
    const traceId = randomUUID();
    const outcome = await this.connection.test(request);
    await this.prisma.$transaction(async (transaction) => {
      if (request.providerId) {
        await transaction.aiProviderConfig.update({
          where: { id: request.providerId },
          data: {
            lastTestStatus: outcome.success
              ? AiConnectionStatus.HEALTHY
              : AiConnectionStatus.FAILED,
            lastTestAt: testedAt,
            lastTestLatencyMs: outcome.latencyMs,
            lastTestErrorCode: outcome.errorCode,
            updatedById: context.identity.user.id,
          },
        });
      }
      await transaction.aiCallLog.create({
        data: {
          traceId,
          providerId: request.providerId,
          providerNameSnapshot: request.providerName,
          modelSnapshot: request.model,
          scene: AiCallScene.CONNECTION_TEST,
          durationMs: outcome.latencyMs,
          result: outcome.success ? AiCallResult.SUCCESS : AiCallResult.FAILURE,
          errorCode: outcome.errorCode,
          errorSummary: outcome.errorSummary,
          actorUserId: context.identity.user.id,
        },
      });
    });
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "ADMIN_AI",
      action: request.providerId
        ? "AI_PROVIDER_TESTED"
        : "AI_PROVIDER_DRAFT_TESTED",
      resourceType: "AI_PROVIDER",
      resourceId: request.providerId ?? undefined,
      result: outcome.success ? AuditResult.SUCCESS : AuditResult.FAILURE,
      traceId,
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      afterSnapshot: {
        providerName: request.providerName,
        model: request.model,
        endpoint: this.normalizeEndpoint(request.endpoint),
        success: outcome.success,
        latencyMs: outcome.latencyMs,
        errorCode: outcome.errorCode,
      },
      errorCode: outcome.errorCode ?? undefined,
    });
    return {
      providerId: request.providerId,
      providerName: request.providerName,
      model: request.model,
      success: outcome.success,
      latencyMs: outcome.latencyMs,
      errorCode: outcome.errorCode,
      errorSummary: outcome.errorSummary,
      testedAt: testedAt.toISOString(),
      traceId,
    };
  }

  private async getProvider(id: string): Promise<AiProviderConfig> {
    const provider = await this.prisma.aiProviderConfig.findUnique({ where: { id } });
    if (!provider) throw new NotFoundException("AI服务配置不存在");
    return provider;
  }

  private async ensureNameAvailable(name: string, excludeId?: string): Promise<void> {
    const existing = await this.prisma.aiProviderConfig.findFirst({
      where: { name: name.trim(), ...(excludeId ? { id: { not: excludeId } } : {}) },
      select: { id: true },
    });
    if (existing) throw new ConflictException("AI服务配置名称已存在");
  }

  private async usageCount(providerId: string): Promise<number> {
    return this.prisma.aiCallLog.count({
      where: {
        providerId,
        createdAt: { gte: new Date(Date.now() - 7 * 86_400_000) },
      },
    });
  }

  private mapProvider(provider: AiProviderConfig, usage: number): AiProviderListItem {
    return {
      id: provider.id,
      name: provider.name,
      vendor: provider.vendor,
      endpoint: provider.endpoint,
      model: provider.model,
      maskedKey: `••••••••••••${provider.keyLast4}`,
      expiresAt: provider.expiresAt?.toISOString().slice(0, 10) ?? null,
      timeoutSeconds: provider.timeoutSeconds,
      retryCount: provider.retryCount,
      enabled: provider.enabled,
      isDefault: provider.isDefault,
      priority: provider.priority,
      lastTestStatus: provider.lastTestStatus,
      lastTestAt: provider.lastTestAt?.toISOString() ?? null,
      lastTestLatencyMs: provider.lastTestLatencyMs,
      lastTestErrorCode: provider.lastTestErrorCode,
      sevenDayUsageCount: usage,
      createdAt: provider.createdAt.toISOString(),
      updatedAt: provider.updatedAt.toISOString(),
    };
  }

  private mapCall(item: {
    id: string;
    traceId: string;
    providerNameSnapshot: string;
    modelSnapshot: string;
    scene: AiCallScene;
    totalTokens: number;
    durationMs: number;
    result: AiCallResult;
    errorCode: string | null;
    errorSummary: string | null;
    createdAt: Date;
  }): AiCallLogListItem {
    return {
      id: item.id,
      traceId: item.traceId,
      providerName: item.providerNameSnapshot,
      model: item.modelSnapshot,
      scene: item.scene,
      totalTokens: item.totalTokens,
      durationMs: item.durationMs,
      result: item.result,
      errorCode: item.errorCode,
      errorSummary: item.errorSummary,
      createdAt: item.createdAt.toISOString(),
    };
  }

  private async recordAudit(
    action: string,
    provider: AiProviderConfig,
    context: AdminRequestContext,
    before?: AiProviderConfig,
  ): Promise<void> {
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "ADMIN_AI",
      action,
      resourceType: "AI_PROVIDER",
      resourceId: provider.id,
      result: AuditResult.SUCCESS,
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      beforeSnapshot: before ? this.auditSnapshot(before) : undefined,
      afterSnapshot: this.auditSnapshot(provider),
    });
  }

  private auditSnapshot(provider: AiProviderConfig): Prisma.InputJsonValue {
    return {
      name: provider.name,
      vendor: provider.vendor,
      endpoint: provider.endpoint,
      model: provider.model,
      maskedKey: `••••••••••••${provider.keyLast4}`,
      expiresAt: provider.expiresAt?.toISOString().slice(0, 10) ?? null,
      timeoutSeconds: provider.timeoutSeconds,
      retryCount: provider.retryCount,
      enabled: provider.enabled,
      isDefault: provider.isDefault,
      priority: provider.priority,
      lastTestStatus: provider.lastTestStatus,
    };
  }

  private normalizeEndpoint(endpoint: string): string {
    return endpoint.trim().replace(/\/+$/, "");
  }

  private toDate(value?: string | null): Date | null {
    return value ? new Date(`${value.slice(0, 10)}T00:00:00.000Z`) : null;
  }

  private round(value: number): number {
    return Math.round(value * 10) / 10;
  }
}
