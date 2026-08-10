import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  AuditResult,
  ProjectRiskLevel,
  type Prisma,
} from "@prisma/client";
import { randomUUID } from "node:crypto";

import type {
  ConfigRiskLevel,
  ProjectOption,
  SystemConfigModule,
  SystemConfigOverview,
  SystemConfigReleaseDetail,
  SystemConfigReleaseItem,
  SystemConfigSnapshot,
  SystemMailSettings,
  SystemNotificationSettings,
  SystemSecuritySettings,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuditService } from "../audit/audit.service";
import { PrismaService } from "../prisma/prisma.service";
import type {
  ListSystemConfigReleasesDto,
  PublishSystemConfigDto,
} from "./dto/system-config.dto";

type DbClient = PrismaService | Prisma.TransactionClient;

const DEFAULT_MAIL: SystemMailSettings = {
  syncIntervalMinutes: 30,
  initialSyncDays: 90,
  subjectKeywords: ["项目周报", "工作周报", "本周进展", "项目进展", "周工作总结"],
  riskKeywords: ["风险", "延期", "回款", "逾期", "投诉", "诉讼", "验收", "审计"],
};

const DEFAULT_SECURITY: SystemSecuritySettings = {
  sessionHours: 8,
  idleTimeoutMinutes: 30,
  loginMaxAttempts: 5,
  loginLockMinutes: 30,
  passwordMinLength: 12,
};

const DEFAULT_NOTIFICATIONS: SystemNotificationSettings = {
  mailboxSyncFailure: true,
  apiKeyExpiry: true,
  apiKeyExpiryDays: 30,
  importFailure: true,
  abnormalLogin: true,
};

@Injectable()
export class SystemConfigService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
  ) {}

  async overview(): Promise<SystemConfigOverview> {
    const release = await this.ensureBaselineRelease();
    const snapshot = await this.buildSnapshot(this.prisma, this.settingsFrom(release.snapshot));
    const monthStart = new Date();
    monthStart.setDate(1);
    monthStart.setHours(0, 0, 0, 0);
    const monthlyChangeCount = await this.prisma.systemConfigRelease.count({
      where: { publishedAt: { gte: monthStart }, changeCount: { gt: 0 } },
    });
    return {
      version: release.version,
      publishedAt: release.publishedAt.toISOString(),
      publishedBy: release.publishedBy?.displayName ?? "系统初始化",
      changeSummary: release.changeSummary,
      activeConfigCount:
        snapshot.categories.filter((item) => item.isActive).length +
        snapshot.levels.filter((item) => item.isActive).length +
        8,
      activeCategoryCount: snapshot.categories.filter((item) => item.isActive).length,
      activeLevelCount: snapshot.levels.filter((item) => item.isActive).length,
      monthlyChangeCount,
      lastMailboxSyncAt: null,
      nextMailboxSyncAt: null,
      authorizedMailboxCount: 0,
      snapshot,
    };
  }

  async projectOptions(): Promise<ProjectOption[]> {
    const projects = await this.prisma.project.findMany({
      where: { status: { not: "ARCHIVED" } },
      select: {
        id: true,
        externalCode: true,
        name: true,
        department: { select: { name: true } },
      },
      orderBy: [{ name: "asc" }],
      take: 1_000,
    });
    return projects.map((project) => ({
      id: project.id,
      externalCode: project.externalCode,
      name: project.name,
      departmentName: project.department?.name ?? null,
    }));
  }

  async runtimeMailSettings(): Promise<SystemMailSettings> {
    const release = await this.ensureBaselineRelease();
    return this.settingsFrom(release.snapshot).mail;
  }

  async publish(
    dto: PublishSystemConfigDto,
    context: AdminRequestContext,
  ): Promise<SystemConfigOverview> {
    this.validatePublish(dto);
    const traceId = randomUUID();
    try {
      await this.prisma.$transaction(async (transaction) => {
        const latest = await transaction.systemConfigRelease.findFirst({
          orderBy: { publishedAt: "desc" },
        });
        const beforeSnapshot = await this.buildSnapshot(
          transaction,
          latest ? this.settingsFrom(latest.snapshot) : undefined,
        );

        const retainedCategoryIds: string[] = [];
        for (const category of dto.categories) {
          const data = {
            code: this.normalizeCode(category.code),
            name: category.name.trim(),
            keywords: this.cleanKeywords(category.keywords),
            colorToken: category.colorToken.toUpperCase(),
            description: category.description?.trim() || null,
            defaultLevel: category.defaultLevel
              ? (category.defaultLevel as ProjectRiskLevel)
              : null,
            sortOrder: category.sortOrder,
            isActive: category.isActive,
          };
          const saved = category.id
            ? await transaction.riskCategory.update({ where: { id: category.id }, data })
            : await transaction.riskCategory.upsert({
                where: { code: data.code },
                create: data,
                update: data,
              });
          retainedCategoryIds.push(saved.id);
        }
        await transaction.riskCategory.updateMany({
          where: { id: { notIn: retainedCategoryIds } },
          data: { isActive: false },
        });

        for (const level of dto.levels) {
          await transaction.riskLevelRule.upsert({
            where: { level: level.level as ProjectRiskLevel },
            create: {
              level: level.level as ProjectRiskLevel,
              displayName: level.displayName.trim(),
              colorToken: level.colorToken.toUpperCase(),
              criteria: level.criteria.trim(),
              keywords: this.cleanKeywords(level.keywords),
              sortOrder: level.sortOrder,
              isActive: level.isActive,
            },
            update: {
              displayName: level.displayName.trim(),
              colorToken: level.colorToken.toUpperCase(),
              criteria: level.criteria.trim(),
              keywords: this.cleanKeywords(level.keywords),
              sortOrder: level.sortOrder,
              isActive: level.isActive,
            },
          });
        }

        const retainedAliasIds: string[] = [];
        for (const alias of dto.aliases) {
          const normalizedAlias = this.normalizeAlias(alias.alias);
          const data = {
            projectId: alias.projectId,
            alias: alias.alias.trim(),
            normalizedAlias,
            source: alias.source.trim() || "系统管理员",
            note: alias.note?.trim() || null,
            isActive: alias.isActive,
          };
          const saved = alias.id
            ? await transaction.projectAlias.update({ where: { id: alias.id }, data })
            : await transaction.projectAlias.upsert({
                where: { normalizedAlias },
                create: data,
                update: data,
              });
          retainedAliasIds.push(saved.id);
        }
        await transaction.projectAlias.updateMany({
          where: { id: { notIn: retainedAliasIds } },
          data: { isActive: false },
        });

        const snapshot = await this.buildSnapshot(transaction, {
          mail: this.cleanMail(dto.mail),
          security: { ...dto.security },
          notifications: { ...dto.notifications },
        });
        const version = this.nextVersion(latest?.version ?? "V12.3");
        const impactScope = [
          "邮箱同步",
          "AI风险提取",
          "Web风险看板",
          "Agent智能对话",
          "新建登录会话",
        ];
        const release = await transaction.systemConfigRelease.create({
          data: {
            version,
            module: dto.module,
            changeCount: dto.changeCount,
            changeSummary: dto.changeSummary.trim(),
            impactScope,
            beforeSnapshot: beforeSnapshot as unknown as Prisma.InputJsonValue,
            snapshot: snapshot as unknown as Prisma.InputJsonValue,
            publishedById: context.identity.user.id,
            traceId,
          },
        });
        await transaction.auditLog.create({
          data: {
            actorUserId: context.identity.user.id,
            module: "SYSTEM_CONFIG",
            action: "SYSTEM_CONFIG_PUBLISHED",
            resourceType: "SYSTEM_CONFIG_RELEASE",
            resourceId: release.id,
            result: AuditResult.SUCCESS,
            traceId,
            clientIp: context.clientIp?.slice(0, 64),
            userAgent: context.userAgent?.slice(0, 500),
            beforeSnapshot: beforeSnapshot as unknown as Prisma.InputJsonValue,
            afterSnapshot: snapshot as unknown as Prisma.InputJsonValue,
            summary: `发布系统配置 ${version} · ${dto.changeSummary.trim()}`.slice(0, 500),
            isSensitive: true,
          },
        });
      });
    } catch (error) {
      await this.audit.record({
        actorUserId: context.identity.user.id,
        module: "SYSTEM_CONFIG",
        action: "SYSTEM_CONFIG_PUBLISH_FAILED",
        resourceType: "SYSTEM_CONFIG_RELEASE",
        result: AuditResult.FAILURE,
        traceId,
        clientIp: context.clientIp,
        userAgent: context.userAgent,
        errorCode: this.errorCode(error),
      });
      if ((error as { code?: string }).code === "P2002") {
        throw new ConflictException("风险类别编码或项目别名重复，请检查后重试");
      }
      throw error;
    }
    return this.overview();
  }

  async releases(query: ListSystemConfigReleasesDto): Promise<SystemConfigReleaseItem[]> {
    const releases = await this.prisma.systemConfigRelease.findMany({
      where: query.module && query.module !== "ALL" ? { module: query.module } : undefined,
      include: { publishedBy: { select: { displayName: true } } },
      orderBy: { publishedAt: "desc" },
      take: query.limit,
    });
    return releases.map((release) => this.mapRelease(release));
  }

  async releaseDetail(id: string): Promise<SystemConfigReleaseDetail> {
    const release = await this.prisma.systemConfigRelease.findUnique({
      where: { id },
      include: { publishedBy: { select: { displayName: true } } },
    });
    if (!release) throw new NotFoundException("配置版本不存在");
    return {
      ...this.mapRelease(release),
      beforeSnapshot: (release.beforeSnapshot as unknown as SystemConfigSnapshot | null) ?? null,
      snapshot: release.snapshot as unknown as SystemConfigSnapshot,
    };
  }

  async runtimeSecurity(): Promise<SystemSecuritySettings> {
    const latest = await this.prisma.systemConfigRelease.findFirst({
      orderBy: { publishedAt: "desc" },
      select: { snapshot: true },
    });
    return this.settingsFrom(latest?.snapshot).security;
  }

  private async ensureBaselineRelease() {
    const existing = await this.prisma.systemConfigRelease.findFirst({
      include: { publishedBy: { select: { displayName: true } } },
      orderBy: { publishedAt: "desc" },
    });
    if (existing) return existing;
    const snapshot = await this.buildSnapshot(this.prisma);
    await this.prisma.systemConfigRelease.upsert({
      where: { version: "V12.3" },
      create: {
        version: "V12.3",
        module: "ALL",
        changeCount: 0,
        changeSummary: "初始化现有风险规则、邮箱识别、项目别名与安全参数",
        impactScope: ["现有配置基线"],
        snapshot: snapshot as unknown as Prisma.InputJsonValue,
        traceId: randomUUID(),
      },
      update: {},
    });
    return (await this.prisma.systemConfigRelease.findFirst({
      include: { publishedBy: { select: { displayName: true } } },
      orderBy: { publishedAt: "desc" },
    }))!;
  }

  private async buildSnapshot(
    client: DbClient,
    settings?: {
      mail: SystemMailSettings;
      security: SystemSecuritySettings;
      notifications: SystemNotificationSettings;
    },
  ): Promise<SystemConfigSnapshot> {
    const [categories, levels, aliases] = await Promise.all([
      client.riskCategory.findMany({
        include: { _count: { select: { risks: true } } },
        orderBy: [{ sortOrder: "asc" }, { name: "asc" }],
      }),
      client.riskLevelRule.findMany({ orderBy: [{ sortOrder: "asc" }] }),
      client.projectAlias.findMany({
        include: {
          project: {
            select: {
              name: true,
              externalCode: true,
              deliveryOwnerName: true,
              manager: { select: { displayName: true } },
            },
          },
        },
        orderBy: [{ project: { name: "asc" } }, { alias: "asc" }],
      }),
    ]);
    return {
      categories: categories.map((category) => ({
        id: category.id,
        code: category.code,
        name: category.name,
        keywords: this.stringArray(category.keywords),
        colorToken: category.colorToken,
        description: category.description,
        defaultLevel: this.configLevel(category.defaultLevel),
        sortOrder: category.sortOrder,
        isActive: category.isActive,
        riskCount: category._count.risks,
      })),
      levels: levels
        .filter((level) => level.level !== ProjectRiskLevel.UNKNOWN)
        .map((level) => ({
          level: level.level as ConfigRiskLevel,
          displayName: level.displayName,
          colorToken: level.colorToken,
          criteria: level.criteria,
          keywords: this.stringArray(level.keywords),
          sortOrder: level.sortOrder,
          isActive: level.isActive,
        })),
      aliases: aliases.map((alias) => ({
        id: alias.id,
        projectId: alias.projectId,
        projectName: alias.project.name,
        projectCode: alias.project.externalCode,
        projectOwnerName:
          alias.project.manager?.displayName ?? alias.project.deliveryOwnerName,
        alias: alias.alias,
        source: alias.source,
        note: alias.note,
        isActive: alias.isActive,
        hitCount: alias.hitCount,
        lastHitAt: alias.lastHitAt?.toISOString() ?? null,
      })),
      mail: settings?.mail ?? { ...DEFAULT_MAIL },
      security: settings?.security ?? { ...DEFAULT_SECURITY },
      notifications: settings?.notifications ?? { ...DEFAULT_NOTIFICATIONS },
    };
  }

  private settingsFrom(snapshot: unknown) {
    const value = (snapshot ?? {}) as Partial<SystemConfigSnapshot>;
    return {
      mail: { ...DEFAULT_MAIL, ...(value.mail ?? {}) },
      security: { ...DEFAULT_SECURITY, ...(value.security ?? {}) },
      notifications: { ...DEFAULT_NOTIFICATIONS, ...(value.notifications ?? {}) },
    };
  }

  private validatePublish(dto: PublishSystemConfigDto): void {
    const categoryCodes = dto.categories.map((item) => this.normalizeCode(item.code));
    if (new Set(categoryCodes).size !== categoryCodes.length) {
      throw new ConflictException("风险类别编码不能重复");
    }
    if (!dto.categories.some((item) => item.isActive)) {
      throw new BadRequestException("至少需要启用一个风险类别");
    }
    const levels = dto.levels.map((item) => item.level);
    if (new Set(levels).size !== 3 || !["HIGH", "MEDIUM", "LOW"].every((level) => levels.includes(level as never))) {
      throw new BadRequestException("高、中、低三级风险规则必须完整");
    }
    const aliases = dto.aliases.map((item) => this.normalizeAlias(item.alias));
    if (aliases.some((item) => !item) || new Set(aliases).size !== aliases.length) {
      throw new ConflictException("项目别名不能为空且不能重复");
    }
  }

  private cleanMail(mail: SystemMailSettings): SystemMailSettings {
    return {
      ...mail,
      subjectKeywords: this.cleanKeywords(mail.subjectKeywords),
      riskKeywords: this.cleanKeywords(mail.riskKeywords),
    };
  }

  private cleanKeywords(values: string[]): string[] {
    return [...new Set(values.map((item) => item.trim()).filter(Boolean))];
  }

  private normalizeAlias(value: string): string {
    return value.normalize("NFKC").trim().toLocaleLowerCase("zh-CN").replace(/\s+/g, "");
  }

  private normalizeCode(value: string): string {
    const code = value.trim().toUpperCase().replace(/[\s-]+/g, "_");
    if (!/^[A-Z][A-Z0-9_]{1,63}$/.test(code)) {
      throw new BadRequestException(`风险类别编码“${value}”格式不正确`);
    }
    return code;
  }

  private nextVersion(version: string): string {
    const match = version.match(/^V(\d+)\.(\d+)$/);
    return match ? `V${match[1]}.${Number(match[2]) + 1}` : "V12.4";
  }

  private stringArray(value: Prisma.JsonValue | null): string[] {
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  }

  private configLevel(level: ProjectRiskLevel | null): ConfigRiskLevel | null {
    return level && level !== ProjectRiskLevel.UNKNOWN ? (level as ConfigRiskLevel) : null;
  }

  private mapRelease(release: {
    id: string;
    version: string;
    module: string;
    changeCount: number;
    changeSummary: string;
    impactScope: Prisma.JsonValue;
    publishedAt: Date;
    traceId: string;
    publishedBy: { displayName: string } | null;
  }): SystemConfigReleaseItem {
    return {
      id: release.id,
      version: release.version,
      module: release.module as SystemConfigModule,
      changeCount: release.changeCount,
      changeSummary: release.changeSummary,
      impactScope: this.stringArray(release.impactScope),
      publishedAt: release.publishedAt.toISOString(),
      publishedBy: release.publishedBy?.displayName ?? "系统初始化",
      traceId: release.traceId,
    };
  }

  private errorCode(error: unknown): string {
    const code = (error as { code?: string })?.code;
    return code ? String(code).slice(0, 128) : "SYSTEM_CONFIG_PUBLISH_FAILED";
  }
}
