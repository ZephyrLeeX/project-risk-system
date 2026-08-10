import {
  BadRequestException,
  ConflictException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  AuditResult,
  MailboxConnectionStatus,
  MailboxEncryption,
  MailboxProvider,
  MailSyncStatus,
  MailSyncTrigger,
  type MailboxConfig,
} from "@prisma/client";
import { randomUUID } from "node:crypto";

import type {
  MailboxConnectionTestResult,
  MailboxOverview,
  MailSyncBatchItem,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { CredentialEncryptionService } from "../ai-providers/credential-encryption.service";
import { AuditService } from "../audit/audit.service";
import type { SessionIdentity } from "../auth/auth.types";
import { PrismaService } from "../prisma/prisma.service";
import { SystemConfigService } from "../system-config/system-config.service";
import type { MailboxConfigDto } from "./dto/mailbox.dto";
import { MailboxConnectionService } from "./mailbox-connection.service";
import { MailSyncProcessorService } from "./mail-sync-processor.service";
import { cleanMailboxKeywords, maskMailboxEmail } from "./mailbox-policy";

@Injectable()
export class MailboxService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly encryption: CredentialEncryptionService,
    private readonly connection: MailboxConnectionService,
    private readonly audit: AuditService,
    private readonly systemConfig: SystemConfigService,
    private readonly syncProcessor: MailSyncProcessorService,
  ) {}

  async overview(identity: SessionIdentity): Promise<MailboxOverview> {
    this.ensureRiskAdmin(identity);
    const [config, user, mailSettings] = await Promise.all([
      this.prisma.mailboxConfig.findUnique({ where: { userId: identity.user.id } }),
      this.prisma.user.findUnique({ where: { id: identity.user.id }, select: { email: true } }),
      this.systemConfig.runtimeMailSettings(),
    ]);
    if (!config) {
      return {
        configured: false,
        provider: "QQ",
        email: user?.email ?? "",
        maskedEmail: user?.email ? maskMailboxEmail(user.email) : null,
        hasAuthCode: false,
        authCodeLast4: null,
        imapHost: "imap.qq.com",
        imapPort: 993,
        encryption: "SSL",
        folder: "INBOX",
        subjectKeywords: ["项目周报", "工作周报", "风险周报"],
        senderRule: "",
        initialSyncWeeks: 4,
        readAttachments: true,
        aiExtractionEnabled: true,
        enabled: false,
        autoSyncEnabled: true,
        autoSyncIntervalMinutes: mailSettings.syncIntervalMinutes,
        connectionStatus: "UNTESTED",
        lastTestAt: null,
        lastTestLatencyMs: null,
        lastTestErrorCode: null,
        lastTestErrorSummary: null,
        lastSyncAt: null,
        lastSyncStatus: null,
        lastSyncNewCount: 0,
        lastSyncSuccessCount: 0,
        lastSyncRiskCandidateCount: 0,
        lastSyncFailedCount: 0,
        nextSyncAt: null,
        uidCursor: null,
        totalSyncedCount: 0,
        totalRiskCandidateCount: 0,
        updatedAt: null,
      };
    }
    const totals = await this.prisma.mailSyncBatch.aggregate({
      where: {
        mailboxConfigId: config.id,
        status: { in: [MailSyncStatus.SUCCESS, MailSyncStatus.PARTIAL] },
      },
      _sum: { successCount: true, riskCandidateCount: true },
    });
    return this.mapOverview(
      config,
      mailSettings.syncIntervalMinutes,
      totals._sum.successCount ?? 0,
      totals._sum.riskCandidateCount ?? 0,
    );
  }

  async save(dto: MailboxConfigDto, context: AdminRequestContext): Promise<MailboxOverview> {
    this.ensureRiskAdmin(context.identity);
    const existing = await this.prisma.mailboxConfig.findUnique({
      where: { userId: context.identity.user.id },
    });
    const normalized = this.normalize(dto);
    if (!existing && !dto.authCode?.trim()) {
      throw new BadRequestException("首次配置邮箱时必须填写邮箱授权码");
    }
    const credential = dto.authCode?.trim()
      ? this.encryption.encrypt(dto.authCode.trim())
      : null;
    const connectionChanged =
      !existing ||
      existing.email !== normalized.email ||
      existing.imapHost !== normalized.imapHost ||
      existing.imapPort !== normalized.imapPort ||
      existing.encryption !== normalized.encryption ||
      existing.folder !== normalized.folder ||
      Boolean(credential);
    const config = await this.prisma.mailboxConfig.upsert({
      where: { userId: context.identity.user.id },
      create: {
        userId: context.identity.user.id,
        ...normalized,
        encryptedAuthCode: credential!.ciphertext,
        authCodeIv: credential!.iv,
        authCodeTag: credential!.authTag,
        authCodeLast4: credential!.last4,
        enabled: true,
        autoSyncEnabled: true,
        connectionStatus: MailboxConnectionStatus.UNTESTED,
      },
      update: {
        ...normalized,
        ...(credential
          ? {
              encryptedAuthCode: credential.ciphertext,
              authCodeIv: credential.iv,
              authCodeTag: credential.authTag,
              authCodeLast4: credential.last4,
            }
          : {}),
        ...(connectionChanged
          ? {
              connectionStatus: MailboxConnectionStatus.UNTESTED,
              lastTestAt: null,
              lastTestLatencyMs: null,
              lastTestErrorCode: null,
              lastTestErrorSummary: null,
            }
          : {}),
      },
    });
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "MAILBOX",
      action: "MAILBOX_CONFIG_SAVED",
      resourceType: "MAILBOX_CONFIG",
      resourceId: config.id,
      result: AuditResult.SUCCESS,
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      beforeSnapshot: existing ? this.safeSnapshot(existing) : undefined,
      afterSnapshot: this.safeSnapshot(config),
      summary: `保存本人邮箱配置 · ${maskMailboxEmail(config.email)}`,
      isSensitive: true,
    });
    return this.overview(context.identity);
  }

  async test(
    dto: MailboxConfigDto,
    context: AdminRequestContext,
  ): Promise<MailboxConnectionTestResult> {
    this.ensureRiskAdmin(context.identity);
    const existing = await this.prisma.mailboxConfig.findUnique({
      where: { userId: context.identity.user.id },
    });
    const normalized = this.normalize(dto);
    const authCode = dto.authCode?.trim() ||
      (existing
        ? this.encryption.decryptCredential(
            {
              ciphertext: existing.encryptedAuthCode,
              iv: existing.authCodeIv,
              authTag: existing.authCodeTag,
            },
            "邮箱授权码解密失败",
          )
        : "");
    if (!authCode) throw new BadRequestException("请填写邮箱授权码后再测试连接");
    const testedAt = new Date();
    const outcome = await this.connection.test({
      email: normalized.email,
      authCode,
      host: normalized.imapHost,
      port: normalized.imapPort,
      encryption: normalized.encryption,
      folder: normalized.folder,
    });
    const matchesSaved = existing && !dto.authCode?.trim() &&
      existing.email === normalized.email &&
      existing.imapHost === normalized.imapHost &&
      existing.imapPort === normalized.imapPort &&
      existing.encryption === normalized.encryption &&
      existing.folder === normalized.folder;
    if (matchesSaved) {
      await this.prisma.mailboxConfig.update({
        where: { id: existing.id },
        data: {
          connectionStatus: outcome.success
            ? MailboxConnectionStatus.HEALTHY
            : MailboxConnectionStatus.FAILED,
          lastTestAt: testedAt,
          lastTestLatencyMs: outcome.latencyMs,
          lastTestErrorCode: outcome.errorCode,
          lastTestErrorSummary: outcome.errorSummary,
        },
      });
    }
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "MAILBOX",
      action: "MAILBOX_CONNECTION_TESTED",
      resourceType: "MAILBOX_CONFIG",
      resourceId: existing?.id ?? context.identity.user.id,
      result: outcome.success ? AuditResult.SUCCESS : AuditResult.FAILURE,
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      errorCode: outcome.errorCode ?? undefined,
      afterSnapshot: {
        email: maskMailboxEmail(normalized.email),
        provider: normalized.provider,
        imapHost: normalized.imapHost,
        imapPort: normalized.imapPort,
        encryption: normalized.encryption,
        folder: normalized.folder,
        latencyMs: outcome.latencyMs,
      },
      summary: outcome.success
        ? `邮箱连接测试通过 · ${maskMailboxEmail(normalized.email)}`
        : `邮箱连接测试失败 · ${outcome.errorSummary}`,
      isSensitive: true,
    });
    return {
      success: outcome.success,
      status: outcome.success ? "HEALTHY" : "FAILED",
      latencyMs: outcome.latencyMs,
      testedAt: testedAt.toISOString(),
      folder: normalized.folder,
      errorCode: outcome.errorCode,
      errorSummary: outcome.errorSummary,
    };
  }

  async setStatus(enabled: boolean, context: AdminRequestContext): Promise<MailboxOverview> {
    this.ensureRiskAdmin(context.identity);
    const existing = await this.getConfig(context.identity.user.id);
    if (existing.enabled === enabled) return this.overview(context.identity);
    const config = await this.prisma.mailboxConfig.update({
      where: { id: existing.id },
      data: { enabled },
    });
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "MAILBOX",
      action: enabled ? "MAILBOX_ENABLED" : "MAILBOX_DISABLED",
      resourceType: "MAILBOX_CONFIG",
      resourceId: config.id,
      result: AuditResult.SUCCESS,
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      beforeSnapshot: { enabled: existing.enabled, email: maskMailboxEmail(existing.email) },
      afterSnapshot: { enabled: config.enabled, email: maskMailboxEmail(config.email) },
      summary: `${enabled ? "恢复" : "停用"}本人邮箱 · ${maskMailboxEmail(config.email)}`,
      isSensitive: true,
    });
    return this.overview(context.identity);
  }

  async startSync(context: AdminRequestContext): Promise<MailSyncBatchItem> {
    this.ensureRiskAdmin(context.identity);
    const config = await this.getConfig(context.identity.user.id);
    if (!config.enabled) throw new BadRequestException("邮箱已停用，请先恢复邮箱");
    if (config.connectionStatus !== MailboxConnectionStatus.HEALTHY) {
      throw new BadRequestException("请先完成邮箱连接测试");
    }
    const running = await this.prisma.mailSyncBatch.findFirst({
      where: {
        mailboxConfigId: config.id,
        status: { in: [MailSyncStatus.QUEUED, MailSyncStatus.RUNNING] },
      },
    });
    if (running) throw new ConflictException("当前已有同步任务正在排队或运行");
    const batch = await this.prisma.mailSyncBatch.create({
      data: {
        code: this.batchCode(),
        mailboxConfigId: config.id,
        trigger: MailSyncTrigger.MANUAL,
        status: MailSyncStatus.QUEUED,
        operatorUserId: context.identity.user.id,
        startUid: config.uidCursor ? config.uidCursor + 1n : null,
      },
    });
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "MAIL_SYNC",
      action: "MAILBOX_SYNC_STARTED",
      resourceType: "MAIL_SYNC_BATCH",
      resourceId: batch.id,
      result: AuditResult.SUCCESS,
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      afterSnapshot: { trigger: batch.trigger, status: batch.status },
      summary: `已创建本人邮箱同步任务 · ${maskMailboxEmail(config.email)}`,
    });
    this.syncProcessor.queue(batch.id);
    return this.mapBatch(batch);
  }

  private ensureRiskAdmin(identity: SessionIdentity): void {
    if (!identity.user.roleCodes.includes("RISK_ADMIN")) {
      throw new ForbiddenException("仅风险管理员可以配置或同步本人邮箱");
    }
  }

  private async getConfig(userId: string): Promise<MailboxConfig> {
    const config = await this.prisma.mailboxConfig.findUnique({ where: { userId } });
    if (!config) throw new NotFoundException("尚未保存个人邮箱配置");
    return config;
  }

  private normalize(dto: MailboxConfigDto) {
    const provider = dto.provider as MailboxProvider;
    return {
      provider,
      email: dto.email.trim().toLocaleLowerCase(),
      imapHost: provider === MailboxProvider.QQ ? "imap.qq.com" : dto.imapHost.trim().toLocaleLowerCase(),
      imapPort: provider === MailboxProvider.QQ ? 993 : dto.imapPort,
      encryption: provider === MailboxProvider.QQ ? MailboxEncryption.SSL : (dto.encryption as MailboxEncryption),
      folder: dto.folder.trim(),
      subjectKeywords: cleanMailboxKeywords(dto.subjectKeywords),
      senderRule: dto.senderRule?.trim() || null,
      initialSyncWeeks: dto.initialSyncWeeks,
      readAttachments: dto.readAttachments,
      aiExtractionEnabled: dto.aiExtractionEnabled,
    };
  }

  private safeSnapshot(config: MailboxConfig) {
    return {
      provider: config.provider,
      email: maskMailboxEmail(config.email),
      imapHost: config.imapHost,
      imapPort: config.imapPort,
      encryption: config.encryption,
      folder: config.folder,
      subjectKeywords: config.subjectKeywords,
      senderRule: config.senderRule,
      initialSyncWeeks: config.initialSyncWeeks,
      readAttachments: config.readAttachments,
      aiExtractionEnabled: config.aiExtractionEnabled,
      enabled: config.enabled,
      autoSyncEnabled: config.autoSyncEnabled,
      connectionStatus: config.connectionStatus,
      hasAuthCode: true,
    };
  }

  private mapOverview(
    config: MailboxConfig,
    intervalMinutes: number,
    totalSyncedCount: number,
    totalRiskCandidateCount: number,
  ): MailboxOverview {
    const nextBase = config.lastSyncAt ?? config.lastTestAt ?? config.updatedAt;
    return {
      configured: true,
      provider: config.provider,
      email: config.email,
      maskedEmail: maskMailboxEmail(config.email),
      hasAuthCode: true,
      authCodeLast4: config.authCodeLast4,
      imapHost: config.imapHost,
      imapPort: config.imapPort,
      encryption: config.encryption,
      folder: config.folder,
      subjectKeywords: Array.isArray(config.subjectKeywords)
        ? config.subjectKeywords.filter((item): item is string => typeof item === "string")
        : [],
      senderRule: config.senderRule ?? "",
      initialSyncWeeks: config.initialSyncWeeks as 1 | 4 | 8 | 12,
      readAttachments: config.readAttachments,
      aiExtractionEnabled: config.aiExtractionEnabled,
      enabled: config.enabled,
      autoSyncEnabled: config.autoSyncEnabled,
      autoSyncIntervalMinutes: intervalMinutes,
      connectionStatus: config.connectionStatus,
      lastTestAt: config.lastTestAt?.toISOString() ?? null,
      lastTestLatencyMs: config.lastTestLatencyMs,
      lastTestErrorCode: config.lastTestErrorCode,
      lastTestErrorSummary: config.lastTestErrorSummary,
      lastSyncAt: config.lastSyncAt?.toISOString() ?? null,
      lastSyncStatus: config.lastSyncStatus,
      lastSyncNewCount: config.lastSyncNewCount,
      lastSyncSuccessCount: config.lastSyncSuccessCount,
      lastSyncRiskCandidateCount: config.lastSyncRiskCandidateCount,
      lastSyncFailedCount: config.lastSyncFailedCount,
      nextSyncAt:
        config.enabled && config.autoSyncEnabled
          ? new Date(nextBase.getTime() + intervalMinutes * 60_000).toISOString()
          : null,
      uidCursor: config.uidCursor?.toString() ?? null,
      totalSyncedCount,
      totalRiskCandidateCount,
      updatedAt: config.updatedAt.toISOString(),
    };
  }

  private mapBatch(batch: {
    id: string;
    code: string;
    trigger: MailSyncTrigger;
    status: MailSyncStatus;
    createdAt: Date;
    startedAt: Date | null;
    finishedAt: Date | null;
    scannedCount: number;
    newCount: number;
    successCount: number;
    skippedCount: number;
    failedCount: number;
    riskCandidateCount: number;
    errorSummary: string | null;
  }): MailSyncBatchItem {
    return {
      id: batch.id,
      code: batch.code,
      trigger: batch.trigger,
      status: batch.status,
      createdAt: batch.createdAt.toISOString(),
      startedAt: batch.startedAt?.toISOString() ?? null,
      finishedAt: batch.finishedAt?.toISOString() ?? null,
      scannedCount: batch.scannedCount,
      newCount: batch.newCount,
      successCount: batch.successCount,
      skippedCount: batch.skippedCount,
      failedCount: batch.failedCount,
      riskCandidateCount: batch.riskCandidateCount,
      errorSummary: batch.errorSummary,
    };
  }

  private batchCode(): string {
    const now = new Date();
    const stamp = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(now).replace(/[- :]/g, "").slice(0, 12);
    return `SYNC-${stamp}-${randomUUID().replace(/-/g, "").slice(0, 6).toUpperCase()}`;
  }
}
