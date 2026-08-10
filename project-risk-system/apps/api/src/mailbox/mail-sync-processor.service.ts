import { Injectable, Logger } from "@nestjs/common";
import {
  AuditResult,
  MailMessageSkipReason,
  MailMessageStatus,
  MailSyncStatus,
  MailSyncTrigger,
  type MailboxConfig,
  type Prisma,
} from "@prisma/client";
import { randomUUID } from "node:crypto";

import { CredentialEncryptionService } from "../ai-providers/credential-encryption.service";
import { AuditService } from "../audit/audit.service";
import { PrismaService } from "../prisma/prisma.service";
import { MailboxConnectionService } from "./mailbox-connection.service";
import { MailContentParserService } from "./mail-content-parser.service";
import { MailProjectMatcherService } from "./mail-project-matcher.service";
import { MailRiskExtractorService } from "./mail-risk-extractor.service";

const MAX_MESSAGES_PER_BATCH = 50;
const MAX_SOURCE_BYTES = 25 * 1024 * 1024;

@Injectable()
export class MailSyncProcessorService {
  private readonly logger = new Logger(MailSyncProcessorService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly credentials: CredentialEncryptionService,
    private readonly connection: MailboxConnectionService,
    private readonly parser: MailContentParserService,
    private readonly matcher: MailProjectMatcherService,
    private readonly extractor: MailRiskExtractorService,
    private readonly audit: AuditService,
  ) {}

  queue(batchId: string): void {
    setImmediate(() => {
      void this.process(batchId).catch((error) => {
        this.logger.error(`邮箱同步批次处理失败 ${batchId}: ${error instanceof Error ? error.message : "unknown"}`);
      });
    });
  }

  async process(batchId: string): Promise<void> {
    const batch = await this.prisma.mailSyncBatch.findUnique({
      where: { id: batchId },
      include: { mailboxConfig: true, targetMessage: true },
    });
    if (!batch || batch.status !== MailSyncStatus.QUEUED) return;
    const startedAt = new Date();
    await this.prisma.mailSyncBatch.update({ where: { id: batch.id }, data: { status: MailSyncStatus.RUNNING, startedAt } });
    const config = batch.mailboxConfig;
    const authCode = this.credentials.decryptCredential({
      ciphertext: config.encryptedAuthCode,
      iv: config.authCodeIv,
      authTag: config.authCodeTag,
    }, "邮箱授权码解密失败");
    let client: Awaited<ReturnType<MailboxConnectionService["connect"]>> | null = null;
    try {
      client = await this.connection.connect({
        email: config.email,
        authCode,
        host: config.imapHost,
        port: config.imapPort,
        encryption: config.encryption,
        folder: config.folder,
      });
      await client.mailboxOpen(config.folder, { readOnly: true });
      const uids = batch.trigger === MailSyncTrigger.RETRY && batch.targetMessage
        ? [Number(batch.targetMessage.imapUid)]
        : await this.findNewUids(client, config);
      const selectedUids = uids.slice(0, MAX_MESSAGES_PER_BATCH);
      let newCount = 0;
      let successCount = 0;
      let skippedCount = 0;
      let failedCount = 0;
      let riskCandidateCount = 0;
      let highestUid = config.uidCursor ?? 0n;

      if (selectedUids.length) {
        for await (const fetched of client.fetch(selectedUids, {
          uid: true,
          envelope: true,
          size: true,
          source: { maxLength: MAX_SOURCE_BYTES },
        }, { uid: true })) {
          const result = await this.processMessage({
            batchId: batch.id,
            config,
            actorUserId: batch.operatorUserId ?? config.userId,
            uid: fetched.uid,
            source: fetched.source,
            size: fetched.size ?? 0,
            retryMessageId: batch.targetMessageId,
          });
          newCount += result.isNew ? 1 : 0;
          successCount += result.status === MailMessageStatus.COMPLETED ? 1 : 0;
          skippedCount += result.status === MailMessageStatus.SKIPPED ? 1 : 0;
          failedCount += result.status === MailMessageStatus.FAILED ? 1 : 0;
          riskCandidateCount += result.riskCandidateCount;
          highestUid = BigInt(fetched.uid) > highestUid ? BigInt(fetched.uid) : highestUid;
        }
      }
      const finishedAt = new Date();
      const finalStatus = failedCount
        ? successCount || skippedCount ? MailSyncStatus.PARTIAL : MailSyncStatus.FAILURE
        : MailSyncStatus.SUCCESS;
      await this.prisma.$transaction([
        this.prisma.mailSyncBatch.update({ where: { id: batch.id }, data: {
          status: finalStatus,
          finishedAt,
          durationMs: finishedAt.getTime() - startedAt.getTime(),
          scannedCount: selectedUids.length,
          newCount,
          successCount,
          skippedCount,
          failedCount,
          riskCandidateCount,
          endUid: highestUid || null,
          errorSummary: failedCount ? `${failedCount}封邮件处理失败，请查看明细后重试` : null,
        } }),
        this.prisma.mailboxConfig.update({ where: { id: config.id }, data: {
          ...(failedCount === 0 && highestUid > 0n ? { uidCursor: highestUid } : {}),
          lastSyncAt: finishedAt,
          lastSyncStatus: finalStatus,
          lastSyncNewCount: newCount,
          lastSyncSuccessCount: successCount,
          lastSyncFailedCount: failedCount,
          lastSyncRiskCandidateCount: riskCandidateCount,
        } }),
      ]);
      await this.recordBatchAudit(batch.id, config, batch.operatorUserId, finalStatus, { scannedCount: selectedUids.length, newCount, successCount, skippedCount, failedCount, riskCandidateCount });
    } catch (error) {
      const finishedAt = new Date();
      const classified = this.connection.classify(error);
      await this.prisma.$transaction([
        this.prisma.mailSyncBatch.update({ where: { id: batch.id }, data: { status: MailSyncStatus.FAILURE, finishedAt, durationMs: finishedAt.getTime() - startedAt.getTime(), errorSummary: classified.errorSummary } }),
        this.prisma.mailboxConfig.update({ where: { id: config.id }, data: { lastSyncAt: finishedAt, lastSyncStatus: MailSyncStatus.FAILURE, lastSyncFailedCount: 1 } }),
      ]);
      await this.recordBatchAudit(batch.id, config, batch.operatorUserId, MailSyncStatus.FAILURE, { errorCode: classified.errorCode, errorSummary: classified.errorSummary });
    } finally {
      if (client) {
        try { await client.logout(); } catch { client.close(); }
      }
    }
  }

  private async findNewUids(client: Awaited<ReturnType<MailboxConnectionService["connect"]>>, config: MailboxConfig): Promise<number[]> {
    const query = config.uidCursor
      ? { uid: `${config.uidCursor + 1n}:*` }
      : { since: new Date(Date.now() - config.initialSyncWeeks * 7 * 86_400_000) };
    const result = await client.search(query, { uid: true });
    return result ? [...result].sort((a, b) => a - b) : [];
  }

  private async processMessage(input: {
    batchId: string;
    config: MailboxConfig;
    actorUserId: string;
    uid: number;
    source?: Buffer;
    size: number;
    retryMessageId: string | null;
  }): Promise<{ isNew: boolean; status: MailMessageStatus; riskCandidateCount: number }> {
    const existingUid = await this.prisma.mailMessage.findUnique({
      where: { mailboxConfigId_imapUid: { mailboxConfigId: input.config.id, imapUid: BigInt(input.uid) } },
    });
    if (existingUid && existingUid.id !== input.retryMessageId) {
      return { isNew: false, status: MailMessageStatus.SKIPPED, riskCandidateCount: 0 };
    }
    const fallbackId = `imap-uid-${input.uid}@local`;
    let recordId = existingUid?.id ?? input.retryMessageId ?? null;
    try {
      if (!input.source || input.size > MAX_SOURCE_BYTES) throw this.safeError("MAIL_TOO_LARGE", "邮件超过25MB安全处理上限");
      const parsed = await this.parser.parse(input.source, fallbackId);
      const duplicate = await this.prisma.mailMessage.findFirst({
        where: { mailboxConfigId: input.config.id, messageId: parsed.messageId, id: { not: recordId ?? undefined } },
      });
      const matchesRule = this.matchesRules(parsed.subject, parsed.senderAddress, input.config);
      const baseData = {
        batchId: input.batchId,
        messageId: parsed.messageId,
        imapUid: BigInt(input.uid),
        subject: parsed.subject,
        senderName: parsed.senderName,
        senderAddress: parsed.senderAddress,
        sentAt: parsed.sentAt,
        processedAt: new Date(),
        sanitizedSummary: parsed.text.slice(0, 2000) || null,
        keyPoints: parsed.keyPoints as Prisma.InputJsonValue,
        attachmentMetadata: parsed.attachments as unknown as Prisma.InputJsonValue,
        processingTrace: parsed.traces as unknown as Prisma.InputJsonValue,
        failureCode: null,
        failureSummary: null,
        skipReason: null,
      };
      if (recordId) {
        await this.prisma.$transaction([
          this.prisma.mailRiskCandidate.deleteMany({ where: { messageId: recordId, status: "PENDING" } }),
          this.prisma.mailMessageProjectMatch.deleteMany({ where: { messageId: recordId } }),
          this.prisma.mailMessage.update({ where: { id: recordId }, data: { ...baseData, status: MailMessageStatus.ANALYZING, retryCount: { increment: 1 } } }),
        ]);
      } else {
        const created = await this.prisma.mailMessage.create({ data: { mailboxConfigId: input.config.id, ...baseData, status: MailMessageStatus.ANALYZING } });
        recordId = created.id;
      }
      if (duplicate || !matchesRule) {
        await this.prisma.mailMessage.update({ where: { id: recordId }, data: {
          status: MailMessageStatus.SKIPPED,
          skipReason: duplicate ? MailMessageSkipReason.DUPLICATE : MailMessageSkipReason.RULE_MISMATCH,
        } });
        return { isNew: true, status: MailMessageStatus.SKIPPED, riskCandidateCount: 0 };
      }
      if (parsed.hasAttachmentFailure) throw this.safeError("ATTACHMENT_PARSE_FAILED", "一个或多个支持格式附件解析失败，未进入AI分析");
      const matches = await this.matcher.match(parsed.subject, parsed.text);
      if (matches.length) {
        await this.prisma.mailMessageProjectMatch.createMany({ data: matches.map((item) => ({
          messageId: recordId!, projectId: item.projectId, matchType: item.matchType, confidence: item.confidence, matchedText: item.matchedText,
        })) });
      }
      const traces = [...parsed.traces, {
        stage: "项目名称匹配",
        status: matches.length ? "COMPLETED" : "SKIPPED",
        detail: matches.length ? `已匹配${matches.length}个标准项目` : "未匹配标准项目，未执行风险提取",
        occurredAt: new Date().toISOString(),
      }];
      let riskCount = 0;
      let keyPoints = parsed.keyPoints;
      if (input.config.aiExtractionEnabled && matches.length) {
        const extracted = await this.extractor.extract({ subject: parsed.subject, text: parsed.text, matches, actorUserId: input.actorUserId });
        keyPoints = extracted.keyPoints.length ? extracted.keyPoints : keyPoints;
        if (extracted.risks.length) {
          await this.prisma.mailRiskCandidate.createMany({ data: extracted.risks.map((risk) => ({ messageId: recordId!, ...risk })) });
        }
        riskCount = extracted.risks.length;
        traces.push({ stage: "AI风险提取", status: "COMPLETED", detail: `完成结构化分析，生成${riskCount}项风险线索`, occurredAt: new Date().toISOString() });
      } else {
        traces.push({ stage: "AI风险提取", status: "SKIPPED", detail: input.config.aiExtractionEnabled ? "未匹配项目，跳过风险提取" : "邮箱配置已关闭AI风险提取", occurredAt: new Date().toISOString() });
      }
      await this.prisma.mailMessage.update({ where: { id: recordId }, data: {
        status: MailMessageStatus.COMPLETED,
        processedAt: new Date(),
        keyPoints: keyPoints as Prisma.InputJsonValue,
        processingTrace: traces as Prisma.InputJsonValue,
      } });
      return { isNew: true, status: MailMessageStatus.COMPLETED, riskCandidateCount: riskCount };
    } catch (error) {
      const safe = this.asSafeError(error);
      if (!recordId) {
        const created = await this.prisma.mailMessage.create({ data: {
          mailboxConfigId: input.config.id,
          batchId: input.batchId,
          messageId: fallbackId,
          imapUid: BigInt(input.uid),
          subject: "邮件处理失败",
          status: MailMessageStatus.FAILED,
          processedAt: new Date(),
          failureCode: safe.code,
          failureSummary: safe.message,
          processingTrace: [{ stage: "邮件处理", status: "FAILED", detail: safe.message, occurredAt: new Date().toISOString() }],
        } });
        recordId = created.id;
      } else {
        await this.prisma.mailMessage.update({ where: { id: recordId }, data: { status: MailMessageStatus.FAILED, processedAt: new Date(), failureCode: safe.code, failureSummary: safe.message } });
      }
      return { isNew: true, status: MailMessageStatus.FAILED, riskCandidateCount: 0 };
    }
  }

  private matchesRules(subject: string, sender: string | null, config: MailboxConfig): boolean {
    const keywords = Array.isArray(config.subjectKeywords)
      ? config.subjectKeywords.filter((item): item is string => typeof item === "string")
      : [];
    const subjectMatches = keywords.some((item) => subject.toLocaleLowerCase().includes(item.toLocaleLowerCase()));
    const senderRule = config.senderRule?.trim().toLocaleLowerCase();
    return subjectMatches && (!senderRule || Boolean(sender?.toLocaleLowerCase().includes(senderRule)));
  }

  private async recordBatchAudit(
    batchId: string,
    config: MailboxConfig,
    actorUserId: string | null,
    status: MailSyncStatus,
    snapshot: Record<string, unknown>,
  ): Promise<void> {
    const failed = status === MailSyncStatus.FAILURE;
    await this.audit.record({
      actorUserId: actorUserId ?? config.userId,
      module: "MAIL_SYNC",
      action: failed ? "MAILBOX_SYNC_FAILED" : "MAILBOX_SYNC_COMPLETED",
      resourceType: "MAIL_SYNC_BATCH",
      resourceId: batchId,
      result: failed ? AuditResult.FAILURE : AuditResult.SUCCESS,
      traceId: randomUUID(),
      afterSnapshot: { status, ...snapshot },
      summary: failed ? "本人邮箱同步失败" : "本人邮箱同步完成",
      isSensitive: true,
    });
  }

  private safeError(code: string, message: string): Error & { code: string } {
    return Object.assign(new Error(message), { code });
  }

  private asSafeError(error: unknown): Error & { code: string } {
    if (error instanceof Error && "code" in error) return error as Error & { code: string };
    return this.safeError("MAIL_PROCESSING_FAILED", "邮件处理失败，请重试或检查附件格式");
  }
}
