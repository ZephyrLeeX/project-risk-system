import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  ActionItemSourceType,
  ActionItemUrgency,
  AuditResult,
  MailMessageSkipReason,
  MailMessageStatus,
  MailRiskCandidateStatus,
  MailSyncStatus,
  MailSyncTrigger,
  ProjectRiskLevel,
  RiskSourceType,
  RiskTimelineEventType,
  type Prisma,
} from "@prisma/client";
import { createHash, randomUUID } from "node:crypto";

import type {
  MailAttachmentItem,
  MailMessageDetail,
  MailMessageListItem,
  MailMessageListResponse,
  MailProcessingTraceItem,
  MailProjectMatchItem,
  MailRiskCandidateItem,
  MailRiskReviewOptions,
  MailSyncBatchDetail,
  MailSyncBatchItem,
  MailSyncSummary,
  PaginatedResponse,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuditService } from "../audit/audit.service";
import type { SessionIdentity } from "../auth/auth.types";
import { PrismaService } from "../prisma/prisma.service";
import { RiskTimelineService } from "../risk-timeline/risk-timeline.service";
import type {
  ListMailBatchesQueryDto,
  ListMailMessagesQueryDto,
  UpdateMailRiskCandidateDto,
} from "./dto/mail-sync.dto";
import { maskMailboxEmail } from "./mailbox-policy";
import { MailSyncProcessorService } from "./mail-sync-processor.service";

const messageInclude = {
  batch: true,
  projectMatches: { include: { project: { select: { name: true } } }, orderBy: { confidence: "desc" as const } },
  riskCandidates: { include: { project: { select: { name: true } }, category: { select: { name: true } } }, orderBy: { createdAt: "asc" as const } },
} satisfies Prisma.MailMessageInclude;

type MessageRecord = Prisma.MailMessageGetPayload<{ include: typeof messageInclude }>;

@Injectable()
export class MailSyncResultsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
    private readonly processor: MailSyncProcessorService,
    private readonly timeline: RiskTimelineService,
  ) {}

  async summary(identity: SessionIdentity): Promise<MailSyncSummary> {
    this.ensureRiskAdmin(identity);
    const config = await this.prisma.mailboxConfig.findUnique({ where: { userId: identity.user.id } });
    if (!config) return {
      configured: false, maskedEmail: null, latestBatch: null,
      latestScannedCount: 0, latestNewCount: 0, latestSuccessCount: 0, latestSkippedCount: 0,
      latestDuplicateCount: 0, latestRuleMismatchCount: 0, latestFailedCount: 0,
      latestRiskCandidateCount: 0, latestPendingRiskCount: 0, historicalFailedCount: 0,
    };
    const latest = await this.prisma.mailSyncBatch.findFirst({ where: { mailboxConfigId: config.id }, orderBy: { createdAt: "desc" } });
    if (!latest) return {
      configured: true, maskedEmail: maskMailboxEmail(config.email), latestBatch: null,
      latestScannedCount: 0, latestNewCount: 0, latestSuccessCount: 0, latestSkippedCount: 0,
      latestDuplicateCount: 0, latestRuleMismatchCount: 0, latestFailedCount: 0,
      latestRiskCandidateCount: 0, latestPendingRiskCount: 0, historicalFailedCount: 0,
    };
    const [duplicates, mismatches, pending, historicalFailed] = await Promise.all([
      this.prisma.mailMessage.count({ where: { batchId: latest.id, skipReason: MailMessageSkipReason.DUPLICATE } }),
      this.prisma.mailMessage.count({ where: { batchId: latest.id, skipReason: MailMessageSkipReason.RULE_MISMATCH } }),
      this.prisma.mailRiskCandidate.count({ where: { message: { mailboxConfigId: config.id, batchId: latest.id }, status: MailRiskCandidateStatus.PENDING } }),
      this.prisma.mailMessage.count({ where: { mailboxConfigId: config.id, status: MailMessageStatus.FAILED } }),
    ]);
    return {
      configured: true,
      maskedEmail: maskMailboxEmail(config.email),
      latestBatch: this.mapBatch(latest),
      latestScannedCount: latest.scannedCount,
      latestNewCount: latest.newCount,
      latestSuccessCount: latest.successCount,
      latestSkippedCount: latest.skippedCount,
      latestDuplicateCount: duplicates,
      latestRuleMismatchCount: mismatches,
      latestFailedCount: latest.failedCount,
      latestRiskCandidateCount: latest.riskCandidateCount,
      latestPendingRiskCount: pending,
      historicalFailedCount: historicalFailed,
    };
  }

  async reviewOptions(identity: SessionIdentity): Promise<MailRiskReviewOptions> {
    await this.getConfig(identity);
    const [projects, categories] = await Promise.all([
      this.prisma.project.findMany({ where: { status: { not: "ARCHIVED" } }, select: { id: true, name: true }, orderBy: { name: "asc" } }),
      this.prisma.riskCategory.findMany({ where: { isActive: true }, select: { id: true, name: true }, orderBy: [{ sortOrder: "asc" }, { name: "asc" }] }),
    ]);
    return {
      projects,
      categories,
      levels: [
        { value: "HIGH", label: "高风险" },
        { value: "MEDIUM", label: "中风险" },
        { value: "LOW", label: "低风险" },
        { value: "UNKNOWN", label: "待判断" },
      ],
    };
  }

  async messages(identity: SessionIdentity, query: ListMailMessagesQueryDto): Promise<MailMessageListResponse> {
    const config = await this.getConfig(identity);
    const filters: Prisma.MailMessageWhereInput[] = [{ mailboxConfigId: config.id }];
    if (query.status) filters.push({ status: query.status });
    if (query.batchId) filters.push({ batchId: query.batchId });
    if (query.withRisk) filters.push({ riskCandidates: { some: {} } });
    if (query.keyword?.trim()) {
      const keyword = query.keyword.trim();
      filters.push({ OR: [
        { subject: { contains: keyword, mode: "insensitive" } },
        { senderName: { contains: keyword, mode: "insensitive" } },
        { senderAddress: { contains: keyword, mode: "insensitive" } },
        { projectMatches: { some: { project: { name: { contains: keyword, mode: "insensitive" } } } } },
      ] });
    }
    const where = { AND: filters } satisfies Prisma.MailMessageWhereInput;
    const [records, total, failed] = await Promise.all([
      this.prisma.mailMessage.findMany({ where, include: messageInclude, orderBy: [{ sentAt: "desc" }, { createdAt: "desc" }], skip: (query.page - 1) * query.pageSize, take: query.pageSize }),
      this.prisma.mailMessage.count({ where }),
      this.prisma.mailMessage.count({ where: { mailboxConfigId: config.id, status: MailMessageStatus.FAILED } }),
    ]);
    return { items: records.map((item) => this.mapMessage(item)), page: query.page, pageSize: query.pageSize, total, historicalFailedCount: failed };
  }

  async message(identity: SessionIdentity, id: string): Promise<MailMessageDetail> {
    const config = await this.getConfig(identity);
    const record = await this.prisma.mailMessage.findFirst({ where: { id, mailboxConfigId: config.id }, include: messageInclude });
    if (!record) throw new NotFoundException("邮件处理记录不存在");
    return this.mapMessageDetail(record);
  }

  async batches(identity: SessionIdentity, query: ListMailBatchesQueryDto): Promise<PaginatedResponse<MailSyncBatchItem>> {
    const config = await this.getConfig(identity);
    const [records, total] = await Promise.all([
      this.prisma.mailSyncBatch.findMany({ where: { mailboxConfigId: config.id }, orderBy: { createdAt: "desc" }, skip: (query.page - 1) * query.pageSize, take: query.pageSize }),
      this.prisma.mailSyncBatch.count({ where: { mailboxConfigId: config.id } }),
    ]);
    return { items: records.map((item) => this.mapBatch(item)), page: query.page, pageSize: query.pageSize, total };
  }

  async batch(identity: SessionIdentity, id: string): Promise<MailSyncBatchDetail> {
    const config = await this.getConfig(identity);
    const record = await this.prisma.mailSyncBatch.findFirst({
      where: { id, mailboxConfigId: config.id },
      include: { operator: { select: { displayName: true } }, messages: { include: messageInclude, orderBy: [{ sentAt: "desc" }, { createdAt: "desc" }] } },
    });
    if (!record) throw new NotFoundException("同步批次不存在");
    return { ...this.mapBatch(record), operatorName: record.operator?.displayName ?? "系统任务", durationMs: record.durationMs, startUid: record.startUid?.toString() ?? null, endUid: record.endUid?.toString() ?? null, messages: record.messages.map((item) => this.mapMessage(item)) };
  }

  async retry(messageId: string, context: AdminRequestContext): Promise<MailSyncBatchItem> {
    const config = await this.getConfig(context.identity);
    const message = await this.prisma.mailMessage.findFirst({ where: { id: messageId, mailboxConfigId: config.id } });
    if (!message) throw new NotFoundException("邮件处理记录不存在");
    if (message.status !== MailMessageStatus.FAILED) throw new BadRequestException("仅处理失败的邮件可以重新处理");
    const running = await this.prisma.mailSyncBatch.findFirst({ where: { mailboxConfigId: config.id, status: { in: [MailSyncStatus.QUEUED, MailSyncStatus.RUNNING] } } });
    if (running) throw new BadRequestException("当前已有同步任务正在排队或运行");
    const batch = await this.prisma.mailSyncBatch.create({ data: {
      code: this.batchCode(), mailboxConfigId: config.id, trigger: MailSyncTrigger.RETRY,
      status: MailSyncStatus.QUEUED, operatorUserId: context.identity.user.id,
      retryOfId: message.batchId, targetMessageId: message.id, startUid: message.imapUid, endUid: message.imapUid,
    } });
    await this.recordAudit("MAIL_MESSAGE_RETRIED", "MAIL_MESSAGE", message.id, context, { batchId: batch.id, code: batch.code });
    this.processor.queue(batch.id);
    return this.mapBatch(batch);
  }

  async updateCandidate(id: string, dto: UpdateMailRiskCandidateDto, context: AdminRequestContext): Promise<MailRiskCandidateItem> {
    const candidate = await this.ownedCandidate(id, context.identity);
    if (candidate.status !== MailRiskCandidateStatus.PENDING) throw new BadRequestException("该风险线索已经处理");
    const [project, category] = await Promise.all([
      this.prisma.project.findUnique({ where: { id: dto.projectId }, select: { id: true } }),
      this.prisma.riskCategory.findFirst({ where: { id: dto.categoryId, isActive: true }, select: { id: true } }),
    ]);
    if (!project || !category) throw new BadRequestException("项目或风险分类无效");
    const updated = await this.prisma.mailRiskCandidate.update({ where: { id }, data: {
      projectId: dto.projectId, categoryId: dto.categoryId, level: dto.level,
      description: dto.description.trim(), evidence: dto.evidence.trim(), suggestion: dto.suggestion.trim(),
      reviewedById: context.identity.user.id, reviewedAt: new Date(),
    }, include: { project: { select: { name: true } }, category: { select: { name: true } } } });
    await this.recordAudit("MAIL_RISK_ADJUSTED", "MAIL_RISK_CANDIDATE", id, context, { projectId: dto.projectId, categoryId: dto.categoryId, level: dto.level });
    return this.mapCandidate(updated);
  }

  async ignoreCandidate(id: string, context: AdminRequestContext): Promise<MailRiskCandidateItem> {
    const candidate = await this.ownedCandidate(id, context.identity);
    if (candidate.status !== MailRiskCandidateStatus.PENDING) throw new BadRequestException("该风险线索已经处理");
    const updated = await this.prisma.mailRiskCandidate.update({ where: { id }, data: { status: MailRiskCandidateStatus.IGNORED, reviewedById: context.identity.user.id, reviewedAt: new Date() }, include: { project: { select: { name: true } }, category: { select: { name: true } } } });
    await this.recordAudit("MAIL_RISK_IGNORED", "MAIL_RISK_CANDIDATE", id, context, { status: updated.status });
    return this.mapCandidate(updated);
  }

  async confirmCandidate(id: string, context: AdminRequestContext): Promise<MailRiskCandidateItem> {
    const candidate = await this.ownedCandidate(id, context.identity);
    if (candidate.status !== MailRiskCandidateStatus.PENDING) throw new BadRequestException("该风险线索已经处理");
    const updated = await this.prisma.$transaction(async (transaction) => {
      const fingerprint = createHash("sha256").update(`MAIL_AI:${candidate.id}`).digest("hex");
      const risk = await transaction.risk.create({ data: {
        projectId: candidate.projectId,
        categoryId: candidate.categoryId,
        title: candidate.description.slice(0, 250),
        description: candidate.description,
        evidence: candidate.evidence,
        level: candidate.level,
        sourceType: RiskSourceType.MAIL_AI,
        sourceRefId: candidate.id,
        reporterUserId: context.identity.user.id,
        reporterNameSource: context.identity.user.displayName,
        suggestion: candidate.suggestion,
        detectedAt: candidate.message.sentAt ?? candidate.createdAt,
        dedupeFingerprint: fingerprint,
      } });
      await this.timeline.record(transaction, { projectId: risk.projectId, riskId: risk.id, eventType: RiskTimelineEventType.RISK_CREATED, title: "周报邮件风险已确认", description: candidate.description, actorUserId: context.identity.user.id, actorNameSource: context.identity.user.displayName, metadata: { source: "MAIL_AI", candidateId: candidate.id, messageId: candidate.messageId } });
      const action = await transaction.actionItem.create({ data: {
        riskId: risk.id,
        projectId: risk.projectId,
        title: candidate.suggestion.slice(0, 250),
        description: candidate.suggestion,
        urgency: candidate.level === ProjectRiskLevel.HIGH ? ActionItemUrgency.HIGH : ActionItemUrgency.NORMAL,
        sourceType: ActionItemSourceType.RISK_SUGGESTION,
        assigneeUserId: context.identity.user.id,
        assigneeNameSource: context.identity.user.displayName,
        createdById: context.identity.user.id,
      } });
      await this.timeline.record(transaction, { projectId: risk.projectId, riskId: risk.id, actionItemId: action.id, eventType: RiskTimelineEventType.ACTION_CREATED, title: "根据周报风险生成待办", description: candidate.suggestion, actorUserId: context.identity.user.id, actorNameSource: context.identity.user.displayName, metadata: { source: "MAIL_AI", candidateId: candidate.id } });
      return transaction.mailRiskCandidate.update({ where: { id }, data: { status: MailRiskCandidateStatus.CONFIRMED, confirmedRiskId: risk.id, reviewedById: context.identity.user.id, reviewedAt: new Date() }, include: { project: { select: { name: true } }, category: { select: { name: true } } } });
    });
    await this.recordAudit("MAIL_RISK_CONFIRMED", "MAIL_RISK_CANDIDATE", id, context, { status: updated.status, confirmedRiskId: updated.confirmedRiskId });
    return this.mapCandidate(updated);
  }

  private async ownedCandidate(id: string, identity: SessionIdentity) {
    const config = await this.getConfig(identity);
    const candidate = await this.prisma.mailRiskCandidate.findFirst({ where: { id, message: { mailboxConfigId: config.id } }, include: { message: true } });
    if (!candidate) throw new NotFoundException("风险线索不存在");
    return candidate;
  }

  private async getConfig(identity: SessionIdentity) {
    this.ensureRiskAdmin(identity);
    const config = await this.prisma.mailboxConfig.findUnique({ where: { userId: identity.user.id } });
    if (!config) throw new NotFoundException("尚未保存个人邮箱配置");
    return config;
  }

  private ensureRiskAdmin(identity: SessionIdentity): void {
    if (!identity.user.roleCodes.includes("RISK_ADMIN")) throw new ForbiddenException("仅风险管理员可以查看本人邮箱同步结果");
  }

  private mapBatch(batch: { id: string; code: string; trigger: MailSyncTrigger; status: MailSyncStatus; createdAt: Date; startedAt: Date | null; finishedAt: Date | null; scannedCount: number; newCount: number; successCount: number; skippedCount: number; failedCount: number; riskCandidateCount: number; errorSummary: string | null }): MailSyncBatchItem {
    return { id: batch.id, code: batch.code, trigger: batch.trigger, status: batch.status, createdAt: batch.createdAt.toISOString(), startedAt: batch.startedAt?.toISOString() ?? null, finishedAt: batch.finishedAt?.toISOString() ?? null, scannedCount: batch.scannedCount, newCount: batch.newCount, successCount: batch.successCount, skippedCount: batch.skippedCount, failedCount: batch.failedCount, riskCandidateCount: batch.riskCandidateCount, errorSummary: batch.errorSummary };
  }

  private mapMessage(record: MessageRecord): MailMessageListItem {
    const pending = record.riskCandidates.filter((item) => item.status === MailRiskCandidateStatus.PENDING).length;
    return {
      id: record.id, batchId: record.batchId, batchCode: record.batch.code, status: record.status,
      subject: record.subject, senderName: record.senderName, senderAddress: record.senderAddress,
      sentAt: record.sentAt?.toISOString() ?? null, processedAt: record.processedAt?.toISOString() ?? null,
      projectMatches: record.projectMatches.map((item) => this.mapMatch(item)),
      riskCandidateCount: record.riskCandidates.length, pendingRiskCount: pending,
      resultLabel: this.resultLabel(record), resultNote: this.resultNote(record, pending), failureSummary: record.failureSummary,
    };
  }

  private mapMessageDetail(record: MessageRecord): MailMessageDetail {
    return { ...this.mapMessage(record), keyPoints: this.stringArray(record.keyPoints), sanitizedSummary: record.sanitizedSummary, attachments: this.jsonArray<MailAttachmentItem>(record.attachmentMetadata), processingTrace: this.jsonArray<MailProcessingTraceItem>(record.processingTrace), riskCandidates: record.riskCandidates.map((item) => this.mapCandidate(item)), retryCount: record.retryCount };
  }

  private mapMatch(item: MessageRecord["projectMatches"][number]): MailProjectMatchItem {
    return { id: item.id, projectId: item.projectId, projectName: item.project.name, matchType: item.matchType, confidence: item.confidence, matchedText: item.matchedText };
  }

  private mapCandidate(item: { id: string; projectId: string; project: { name: string }; categoryId: string; category: { name: string }; level: ProjectRiskLevel; description: string; evidence: string; suggestion: string; confidence: number; status: MailRiskCandidateStatus; confirmedRiskId: string | null; reviewedAt: Date | null }): MailRiskCandidateItem {
    return { id: item.id, projectId: item.projectId, projectName: item.project.name, categoryId: item.categoryId, categoryName: item.category.name, level: item.level, levelLabel: this.levelLabel(item.level), description: item.description, evidence: item.evidence, suggestion: item.suggestion, confidence: item.confidence, status: item.status, confirmedRiskId: item.confirmedRiskId, reviewedAt: item.reviewedAt?.toISOString() ?? null };
  }

  private resultLabel(record: MessageRecord): string {
    if (record.status === MailMessageStatus.FAILED) return record.failureSummary ?? "处理失败";
    if (record.status === MailMessageStatus.ANALYZING) return "AI分析中";
    if (record.status === MailMessageStatus.SKIPPED) return record.skipReason === MailMessageSkipReason.DUPLICATE ? "重复邮件" : "不符合周报规则";
    return record.riskCandidates.length ? `提取${record.riskCandidates.length}项风险` : "未发现新增风险";
  }

  private resultNote(record: MessageRecord, pending: number): string {
    if (record.status === MailMessageStatus.FAILED) return "等待风险管理员重试";
    if (record.status === MailMessageStatus.ANALYZING) return "已进入分析队列";
    if (record.status === MailMessageStatus.SKIPPED) return record.skipReason === MailMessageSkipReason.DUPLICATE ? "按Message-ID去重跳过" : "主题或发件人未命中识别规则";
    return pending ? `${pending}项待风险管理员确认` : "邮件分析完成";
  }

  private levelLabel(level: ProjectRiskLevel): string {
    return { HIGH: "高风险", MEDIUM: "中风险", LOW: "低风险", UNKNOWN: "待判断" }[level];
  }

  private stringArray(value: Prisma.JsonValue | null): string[] {
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  }

  private jsonArray<T>(value: Prisma.JsonValue | null): T[] {
    return Array.isArray(value) ? value as unknown as T[] : [];
  }

  private batchCode(): string {
    const stamp = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date()).replace(/[- :]/g, "").slice(0, 12);
    return `SYNC-${stamp}-${randomUUID().replace(/-/g, "").slice(0, 6).toUpperCase()}`;
  }

  private async recordAudit(action: string, resourceType: string, resourceId: string, context: AdminRequestContext, snapshot: Record<string, unknown>): Promise<void> {
    await this.audit.record({ actorUserId: context.identity.user.id, module: "MAIL_SYNC", action, resourceType, resourceId, result: AuditResult.SUCCESS, traceId: randomUUID(), clientIp: context.clientIp, userAgent: context.userAgent, afterSnapshot: snapshot as Prisma.InputJsonObject, summary: action.replace(/_/g, " "), isSensitive: true });
  }
}
