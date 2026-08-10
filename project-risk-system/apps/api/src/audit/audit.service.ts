import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  AuditResult,
  DataScopeType,
  type Prisma,
} from "@prisma/client";
import { randomUUID } from "node:crypto";
import ExcelJS = require("exceljs");

import type {
  AuditActionGroup,
  AuditLogDetail,
  AuditLogIntegrity,
  AuditLogListItem,
  AuditLogOptions,
  AuditLogSummary,
  AuditModuleKey,
  PaginatedResponse,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import type { SessionIdentity } from "../auth/auth.types";
import { PrismaService } from "../prisma/prisma.service";
import { DataScopeService } from "../rbac/data-scope.service";
import {
  auditActionGroup,
  auditActionGroupLabel,
  auditActionLabel,
  auditModuleKey,
  auditModuleLabel,
  auditSnapshotSummary,
  clientLabel,
  isSensitiveAuditEvent,
  maskClientIp,
  sanitizeAuditSnapshot,
} from "./audit-policy";
import type {
  AuditFilterDto,
  ExportAuditLogsDto,
  ListAuditLogsQueryDto,
} from "./dto/audit-log.dto";

export interface AuditEvent {
  actorUserId?: string;
  module: string;
  action: string;
  resourceType: string;
  resourceId?: string;
  result: AuditResult;
  traceId: string;
  clientIp?: string;
  userAgent?: string;
  beforeSnapshot?: Prisma.InputJsonValue;
  afterSnapshot?: Prisma.InputJsonValue;
  errorCode?: string;
  summary?: string;
  isSensitive?: boolean;
}

export interface ExportedAuditFile {
  buffer: Buffer;
  fileName: string;
  mimeType: string;
  count: number;
}

const AUDIT_INCLUDE = {
  actor: {
    select: {
      username: true,
      displayName: true,
      roles: {
        where: { role: { enabled: true } },
        select: { role: { select: { name: true, code: true } } },
        take: 1,
      },
    },
  },
} satisfies Prisma.AuditLogInclude;

type AuditRecord = Prisma.AuditLogGetPayload<{ include: typeof AUDIT_INCLUDE }>;

const MODULE_FILTERS: Record<Exclude<AuditModuleKey, "ALL">, string[]> = {
  AUTH: ["AUTH"],
  PERMISSION: ["ADMIN_USER", "ADMIN_ROLE"],
  MAILBOX: ["MAILBOX", "MAIL_SYNC", "MAIL_MESSAGE"],
  AI: ["AI", "ADMIN_AI"],
  RISK: ["RISK", "TODO"],
  IMPORT: ["IMPORT"],
  CONFIG: ["SYSTEM_CONFIG"],
  AUDIT: ["AUDIT"],
  OTHER: [],
};

const ACTION_PATTERNS: Record<Exclude<AuditActionGroup, "ALL" | "OTHER">, string[]> = {
  CREATE: ["CREATE", "CREATED", "REPORT", "STARTED"],
  UPDATE: ["UPDATE", "UPDATED", "CHANGED", "STATUS", "RESOLVED", "REOPENED", "MATCHED", "UNMATCHED", "UNLOCK"],
  TEST: ["TEST"],
  LOGIN: ["LOGIN", "LOGOUT", "PASSWORD"],
  PUBLISH: ["PUBLISH", "PUBLISHED", "CONFIRM", "CONFIRMED"],
  ROLLBACK: ["ROLLBACK", "ROLLED_BACK"],
  EXPORT: ["EXPORT"],
};

@Injectable()
export class AuditService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly dataScopes: DataScopeService,
  ) {}

  async record(event: AuditEvent): Promise<void> {
    await this.prisma.auditLog.create({
      data: {
        actorUserId: event.actorUserId,
        module: event.module,
        action: event.action,
        resourceType: event.resourceType,
        resourceId: event.resourceId,
        result: event.result,
        traceId: event.traceId,
        clientIp: event.clientIp?.slice(0, 64),
        userAgent: event.userAgent?.slice(0, 500),
        beforeSnapshot: event.beforeSnapshot,
        afterSnapshot: event.afterSnapshot,
        errorCode: event.errorCode,
        summary: (event.summary ?? this.defaultSummary(event)).slice(0, 500),
        isSensitive:
          event.isSensitive ?? isSensitiveAuditEvent(event.module, event.action),
      },
    });
  }

  async summary(identity: SessionIdentity): Promise<AuditLogSummary> {
    const scope = await this.scopeWhere(identity);
    const today = this.startOfDay(new Date());
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const todayWhere = { AND: [scope, { createdAt: { gte: today, lt: tomorrow } }] } satisfies Prisma.AuditLogWhereInput;
    const yesterdayWhere = { AND: [scope, { createdAt: { gte: yesterday, lt: today } }] } satisfies Prisma.AuditLogWhereInput;

    const [
      todayCount,
      yesterdayCount,
      failedCount,
      sensitiveCount,
      activeActors,
      systemAdminActors,
    ] = await Promise.all([
      this.prisma.auditLog.count({ where: todayWhere }),
      this.prisma.auditLog.count({ where: yesterdayWhere }),
      this.prisma.auditLog.count({ where: { AND: [todayWhere, { result: AuditResult.FAILURE }] } }),
      this.prisma.auditLog.count({ where: { AND: [todayWhere, { isSensitive: true }] } }),
      this.prisma.auditLog.findMany({
        where: { AND: [todayWhere, { actorUserId: { not: null } }] },
        select: { actorUserId: true },
        distinct: ["actorUserId"],
      }),
      this.prisma.auditLog.findMany({
        where: {
          AND: [todayWhere, { actor: { roles: { some: { role: { code: "SYSTEM_ADMIN" } } } } }],
        },
        select: { actorUserId: true },
        distinct: ["actorUserId"],
      }),
    ]);

    return {
      todayCount,
      yesterdayCount,
      dayChange: todayCount - yesterdayCount,
      failedCount,
      sensitiveCount,
      activeActorCount: activeActors.length,
      systemAdminActorCount: systemAdminActors.length,
    };
  }

  async options(identity: SessionIdentity): Promise<AuditLogOptions> {
    const scope = await this.scopeWhere(identity);
    const [moduleRows, actionRows] = await Promise.all([
      this.prisma.auditLog.groupBy({ by: ["module"], where: scope, _count: { _all: true } }),
      this.prisma.auditLog.groupBy({ by: ["action"], where: scope, _count: { _all: true } }),
    ]);
    const moduleCounts = new Map<AuditModuleKey, number>();
    moduleRows.forEach((row) => {
      const key = auditModuleKey(row.module);
      moduleCounts.set(key, (moduleCounts.get(key) ?? 0) + row._count._all);
    });
    const actionCounts = new Map<AuditActionGroup, number>();
    actionRows.forEach((row) => {
      const key = auditActionGroup(row.action);
      actionCounts.set(key, (actionCounts.get(key) ?? 0) + row._count._all);
    });

    return {
      modules: [...moduleCounts.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([value, count]) => ({ value, label: auditModuleLabel(value), count })),
      actions: [...actionCounts.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([value, count]) => ({ value, label: auditActionGroupLabel(value), count })),
    };
  }

  async list(
    identity: SessionIdentity,
    query: ListAuditLogsQueryDto,
  ): Promise<PaginatedResponse<AuditLogListItem>> {
    const where = await this.queryWhere(identity, query);
    const [items, total] = await Promise.all([
      this.prisma.auditLog.findMany({
        where,
        include: AUDIT_INCLUDE,
        orderBy: [{ createdAt: "desc" }, { id: "desc" }],
        skip: (query.page - 1) * query.pageSize,
        take: query.pageSize,
      }),
      this.prisma.auditLog.count({ where }),
    ]);
    return {
      items: items.map((item) => this.mapListItem(item)),
      page: query.page,
      pageSize: query.pageSize,
      total,
    };
  }

  async detail(identity: SessionIdentity, id: string): Promise<AuditLogDetail> {
    const scope = await this.scopeWhere(identity);
    const item = await this.prisma.auditLog.findFirst({
      where: { AND: [scope, { id }] },
      include: AUDIT_INCLUDE,
    });
    if (!item) throw new NotFoundException("审计事件不存在或不在当前授权范围内");
    const listItem = this.mapListItem(item);
    return {
      ...listItem,
      beforeSnapshot: sanitizeAuditSnapshot(item.beforeSnapshot),
      afterSnapshot: sanitizeAuditSnapshot(item.afterSnapshot),
      beforeSummary: auditSnapshotSummary(item.beforeSnapshot),
      afterSummary: auditSnapshotSummary(item.afterSnapshot),
      context: item.errorCode
        ? `操作未成功，错误码：${item.errorCode}。敏感凭据和原始响应未写入审计日志。`
        : "操作记录已写入只追加审计链，变更快照已按安全规则脱敏。",
      previousHash: item.previousHash,
      integrityHash: item.integrityHash,
    };
  }

  async integrity(): Promise<AuditLogIntegrity> {
    const rows = await this.prisma.$queryRaw<Array<{ id: string; valid: boolean }>>`
      WITH ordered_logs AS (
        SELECT
          log_entry.*,
          lag(log_entry."integrityHash") OVER (
            ORDER BY log_entry."createdAt", log_entry."id"
          ) AS expected_previous_hash
        FROM "audit_logs" log_entry
      )
      SELECT
        ordered_logs."id"::text AS id,
        (
          ordered_logs."integrityHash" IS NOT NULL
          AND ordered_logs."previousHash" IS NOT DISTINCT FROM ordered_logs.expected_previous_hash
          AND ordered_logs."integrityHash" = audit_log_compute_hash(
            ordered_logs."id",
            ordered_logs."actorUserId",
            ordered_logs."module",
            ordered_logs."action",
            ordered_logs."resourceType",
            ordered_logs."resourceId",
            ordered_logs."result"::text,
            ordered_logs."traceId",
            ordered_logs."clientIp",
            ordered_logs."userAgent",
            ordered_logs."beforeSnapshot",
            ordered_logs."afterSnapshot",
            ordered_logs."errorCode",
            ordered_logs."summary",
            ordered_logs."isSensitive",
            ordered_logs."previousHash",
            ordered_logs."createdAt"
          )
        ) AS valid
      FROM ordered_logs
      ORDER BY ordered_logs."createdAt", ordered_logs."id"
    `;
    const firstBroken = rows.find((row) => !row.valid) ?? null;
    return {
      status: firstBroken ? "INVALID" : "VALID",
      totalRecords: rows.length,
      verifiedRecords: rows.filter((row) => row.valid).length,
      firstBrokenEventId: firstBroken?.id ?? null,
      lastVerifiedAt: new Date().toISOString(),
      appendOnly: true,
    };
  }

  async export(
    dto: ExportAuditLogsDto,
    context: AdminRequestContext,
  ): Promise<ExportedAuditFile> {
    const traceId = randomUUID();
    try {
      const where = await this.queryWhere(context.identity, dto);
      const records = await this.prisma.auditLog.findMany({
        where,
        include: AUDIT_INCLUDE,
        orderBy: [{ createdAt: "desc" }, { id: "desc" }],
        take: 10_001,
      });
      if (records.length > 10_000) {
        throw new BadRequestException("当前筛选结果超过10000条，请缩小日期或筛选范围后重试");
      }
      const items = records.map((item) => this.mapListItem(item));
      const file = dto.format === "CSV" ? this.toCsv(items) : await this.toXlsx(items);
      await this.record({
        actorUserId: context.identity.user.id,
        module: "AUDIT",
        action: "AUDIT_LOG_EXPORTED",
        resourceType: "AUDIT_EXPORT",
        result: AuditResult.SUCCESS,
        traceId,
        clientIp: context.clientIp,
        userAgent: context.userAgent,
        summary: `导出${items.length}条审计记录 · ${dto.reason.trim()}`,
        isSensitive: true,
        afterSnapshot: {
          format: dto.format,
          reason: dto.reason.trim(),
          count: items.length,
          module: dto.module,
          action: dto.action,
          result: dto.result ?? null,
          dateRange: dto.dateRange,
        },
      });
      return file;
    } catch (error) {
      await this.record({
        actorUserId: context.identity.user.id,
        module: "AUDIT",
        action: "AUDIT_LOG_EXPORT_FAILED",
        resourceType: "AUDIT_EXPORT",
        result: AuditResult.FAILURE,
        traceId,
        clientIp: context.clientIp,
        userAgent: context.userAgent,
        errorCode: error instanceof BadRequestException ? "EXPORT_SCOPE_TOO_LARGE" : "EXPORT_FAILED",
        summary: `审计日志导出失败 · ${dto.reason.trim()}`,
        isSensitive: true,
      });
      throw error;
    }
  }

  private async queryWhere(
    identity: SessionIdentity,
    query: AuditFilterDto,
  ): Promise<Prisma.AuditLogWhereInput> {
    const conditions: Prisma.AuditLogWhereInput[] = [await this.scopeWhere(identity)];
    const dateFilter = this.dateFilter(query);
    if (dateFilter) conditions.push({ createdAt: dateFilter });
    if (query.result) conditions.push({ result: query.result as AuditResult });
    if (query.sensitiveOnly) conditions.push({ isSensitive: true });

    if (query.module && query.module !== "ALL") {
      if (query.module === "OTHER") {
        const known = Object.values(MODULE_FILTERS).flat();
        conditions.push({ module: { notIn: known } });
      } else {
        conditions.push({ module: { in: MODULE_FILTERS[query.module] } });
      }
    }
    if (query.action && query.action !== "ALL") {
      const knownPatterns = Object.values(ACTION_PATTERNS).flat();
      const patterns = query.action === "OTHER" ? [] : ACTION_PATTERNS[query.action];
      conditions.push(
        query.action === "OTHER"
          ? { NOT: { OR: knownPatterns.map((pattern) => ({ action: { contains: pattern, mode: "insensitive" } })) } }
          : { OR: patterns.map((pattern) => ({ action: { contains: pattern, mode: "insensitive" } })) },
      );
    }
    const keyword = query.keyword?.trim();
    if (keyword) {
      conditions.push({
        OR: [
          { action: { contains: keyword, mode: "insensitive" } },
          { module: { contains: keyword, mode: "insensitive" } },
          { resourceType: { contains: keyword, mode: "insensitive" } },
          { resourceId: { contains: keyword, mode: "insensitive" } },
          { traceId: { contains: keyword, mode: "insensitive" } },
          { summary: { contains: keyword, mode: "insensitive" } },
          { actor: { displayName: { contains: keyword, mode: "insensitive" } } },
          { actor: { username: { contains: keyword, mode: "insensitive" } } },
        ],
      });
    }
    return { AND: conditions };
  }

  private async scopeWhere(identity: SessionIdentity): Promise<Prisma.AuditLogWhereInput> {
    if (identity.user.roleCodes.includes("SYSTEM_ADMIN")) return {};
    const projects = await this.prisma.project.findMany({
      where: this.dataScopes.forUser(
        identity.user.id,
        identity.user.dataScope as DataScopeType,
      ),
      select: { id: true },
    });
    const projectIds = projects.map((project) => project.id);
    if (!projectIds.length) return { id: "00000000-0000-0000-0000-000000000000" };
    const [risks, actionItems] = await Promise.all([
      this.prisma.risk.findMany({ where: { projectId: { in: projectIds } }, select: { id: true } }),
      this.prisma.actionItem.findMany({ where: { projectId: { in: projectIds } }, select: { id: true } }),
    ]);
    return {
      OR: [
        { resourceType: "PROJECT", resourceId: { in: projectIds } },
        { resourceType: "RISK", resourceId: { in: risks.map((item) => item.id) } },
        { resourceType: "ACTION_ITEM", resourceId: { in: actionItems.map((item) => item.id) } },
      ],
    };
  }

  private dateFilter(query: AuditFilterDto): Prisma.DateTimeFilter | null {
    const now = new Date();
    if (query.dateRange === "CUSTOM") {
      if (!query.startDate || !query.endDate) {
        throw new BadRequestException("自定义日期范围必须同时提供开始日期和结束日期");
      }
      const start = new Date(`${query.startDate}T00:00:00+08:00`);
      const end = new Date(`${query.endDate}T00:00:00+08:00`);
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
        throw new BadRequestException("开始日期不能晚于结束日期");
      }
      end.setDate(end.getDate() + 1);
      return { gte: start, lt: end };
    }
    const start = this.startOfDay(now);
    if (query.dateRange === "7_DAYS") start.setDate(start.getDate() - 6);
    if (query.dateRange === "30_DAYS") start.setDate(start.getDate() - 29);
    const end = new Date(this.startOfDay(now));
    end.setDate(end.getDate() + 1);
    return { gte: start, lt: end };
  }

  private startOfDay(value: Date): Date {
    const start = new Date(value);
    start.setHours(0, 0, 0, 0);
    return start;
  }

  private mapListItem(item: AuditRecord): AuditLogListItem {
    const module = auditModuleKey(item.module);
    const role = item.actor?.roles[0]?.role ?? null;
    return {
      id: item.id,
      eventId: this.eventId(item),
      createdAt: item.createdAt.toISOString(),
      actorName: item.actor?.displayName ?? "系统任务",
      actorAccount: item.actor?.username ?? null,
      actorRole: role?.name ?? null,
      module,
      moduleLabel: auditModuleLabel(module),
      rawModule: item.module,
      action: item.action,
      actionLabel: auditActionLabel(item.action),
      actionGroup: auditActionGroup(item.action),
      resourceType: item.resourceType,
      resourceId: item.resourceId,
      resourceLabel: `${item.resourceType}${item.resourceId ? ` / ${item.resourceId}` : ""}`,
      summary: item.summary?.trim() || this.defaultSummary(item),
      result: item.result,
      traceId: item.traceId,
      clientIp: maskClientIp(item.clientIp),
      client: clientLabel(item.userAgent),
      errorCode: item.errorCode,
      isSensitive: item.isSensitive || isSensitiveAuditEvent(item.module, item.action),
    };
  }

  private eventId(item: Pick<AuditRecord, "id" | "createdAt">): string {
    const value = item.createdAt;
    const pad = (number: number) => String(number).padStart(2, "0");
    return `AUD-${value.getFullYear()}${pad(value.getMonth() + 1)}${pad(value.getDate())}-${pad(value.getHours())}${pad(value.getMinutes())}${pad(value.getSeconds())}-${item.id.slice(0, 6).toUpperCase()}`;
  }

  private defaultSummary(event: {
    action: string;
    resourceType: string;
    resourceId?: string | null;
  }): string {
    return `${auditActionLabel(event.action)} · ${event.resourceType}${event.resourceId ? `/${event.resourceId}` : ""}`;
  }

  private toCsv(items: AuditLogListItem[]): ExportedAuditFile {
    const headers = ["时间", "事件编号", "模块", "操作", "操作人", "账号", "资源", "摘要", "结果", "客户端IP", "客户端", "Trace ID", "错误码", "敏感操作"];
    const rows = items.map((item) => [
      item.createdAt,
      item.eventId,
      item.moduleLabel,
      item.actionLabel,
      item.actorName,
      item.actorAccount ?? "",
      item.resourceLabel,
      item.summary,
      item.result === "SUCCESS" ? "成功" : "失败",
      item.clientIp,
      item.client,
      item.traceId,
      item.errorCode ?? "",
      item.isSensitive ? "是" : "否",
    ]);
    const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const csv = `\uFEFF${[headers, ...rows].map((row) => row.map(escape).join(",")).join("\r\n")}`;
    return {
      buffer: Buffer.from(csv, "utf8"),
      fileName: `审计日志_${this.fileTimestamp()}.csv`,
      mimeType: "text/csv; charset=utf-8",
      count: items.length,
    };
  }

  private async toXlsx(items: AuditLogListItem[]): Promise<ExportedAuditFile> {
    const workbook = new ExcelJS.Workbook();
    workbook.creator = "项目风险管理平台";
    workbook.created = new Date();
    const sheet = workbook.addWorksheet("审计日志", { views: [{ state: "frozen", ySplit: 1 }] });
    sheet.columns = [
      { header: "时间", key: "createdAt", width: 23 },
      { header: "事件编号", key: "eventId", width: 34 },
      { header: "模块", key: "module", width: 16 },
      { header: "操作", key: "action", width: 20 },
      { header: "操作人", key: "actor", width: 16 },
      { header: "账号", key: "account", width: 18 },
      { header: "资源", key: "resource", width: 40 },
      { header: "摘要", key: "summary", width: 56 },
      { header: "结果", key: "result", width: 12 },
      { header: "客户端IP", key: "ip", width: 18 },
      { header: "客户端", key: "client", width: 18 },
      { header: "Trace ID", key: "traceId", width: 38 },
      { header: "错误码", key: "errorCode", width: 24 },
      { header: "敏感操作", key: "sensitive", width: 12 },
    ];
    items.forEach((item) => sheet.addRow({
      createdAt: item.createdAt,
      eventId: item.eventId,
      module: item.moduleLabel,
      action: item.actionLabel,
      actor: item.actorName,
      account: item.actorAccount ?? "",
      resource: item.resourceLabel,
      summary: item.summary,
      result: item.result === "SUCCESS" ? "成功" : "失败",
      ip: item.clientIp,
      client: item.client,
      traceId: item.traceId,
      errorCode: item.errorCode ?? "",
      sensitive: item.isSensitive ? "是" : "否",
    }));
    sheet.getRow(1).font = { bold: true, color: { argb: "FFFFFFFF" } };
    sheet.getRow(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF176FC8" } };
    sheet.autoFilter = { from: "A1", to: "N1" };
    const buffer = Buffer.from(await workbook.xlsx.writeBuffer());
    return {
      buffer,
      fileName: `审计日志_${this.fileTimestamp()}.xlsx`,
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      count: items.length,
    };
  }

  private fileTimestamp(): string {
    const now = new Date();
    const pad = (value: number) => String(value).padStart(2, "0");
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  }
}
