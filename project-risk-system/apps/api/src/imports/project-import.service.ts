import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import {
  ImportBatchStatus,
  type ImportRowStatus,
  Prisma,
  type ActionItem,
  type Project,
  type ProjectRiskLevel,
  ProjectStatus,
  type Risk,
  RiskSourceType,
} from "@prisma/client";
import { createHash, randomUUID } from "node:crypto";
import * as fs from "node:fs/promises";
import * as path from "node:path";

import type {
  LegalMatterRowItem,
  PaginatedResponse,
  ProjectImportBatchDetail,
  ProjectImportBatchSummary,
  ProjectImportRowItem,
  ProjectOption,
  SupplementalCollectionRowItem,
} from "@risk-platform/contracts";

import type { AdminRequestContext } from "../admin/admin.types";
import { AuditService } from "../audit/audit.service";
import { PrismaService } from "../prisma/prisma.service";
import { RiskTimelineService } from "../risk-timeline/risk-timeline.service";
import {
  defaultAssigneeForRisk,
  urgencyForRisk,
} from "../todos/todo-policy";
import type {
  ConfirmProjectImportDto,
  ListImportBatchesQueryDto,
  MatchSupplementalCollectionDto,
} from "./dto/project-import.dto";
import {
  type ParsedProjectImportRow,
  type ParsedLegalMatterRow,
  type ParsedSupplementalCollectionRow,
  ProjectListParser,
} from "./project-list.parser";

const BATCH_SUMMARY_INCLUDE = {
  uploadedBy: {
    select: {
      displayName: true,
    },
  },
} satisfies Prisma.ImportBatchInclude;

const BATCH_DETAIL_INCLUDE = {
  ...BATCH_SUMMARY_INCLUDE,
  rows: {
    orderBy: {
      rowNumber: "asc",
    },
  },
  supplementalRows: {
    include: {
      project: {
        select: {
          id: true,
          externalCode: true,
          name: true,
          department: {
            select: {
              name: true,
            },
          },
        },
      },
    },
    orderBy: {
      rowNumber: "asc",
    },
  },
  legalRows: {
    orderBy: {
      rowNumber: "asc",
    },
  },
} satisfies Prisma.ImportBatchInclude;

type BatchSummaryRecord = Prisma.ImportBatchGetPayload<{
  include: typeof BATCH_SUMMARY_INCLUDE;
}>;

type BatchDetailRecord = Prisma.ImportBatchGetPayload<{
  include: typeof BATCH_DETAIL_INCLUDE;
}>;

interface ProjectSnapshot {
  externalCode: string | null;
  importKey: string | null;
  name: string;
  alias: string | null;
  status: "DELIVERY" | "COMPLETED" | "ARCHIVED";
  departmentId: string | null;
  managerId: string | null;
  deliveryOwnerName: string | null;
  annualPlanAmount: string | null;
  actualCollectedAmount: string | null;
  remainingAmount: string | null;
  monthlyCollections: Prisma.JsonValue | null;
  monthAttributes: Prisma.JsonValue | null;
  collectionRiskLevel: ProjectRiskLevel;
  collectionProgress: string | null;
  lastImportedAt: string | null;
  sourceVersion: number;
}

interface RiskSnapshot {
  projectId: string;
  categoryId: string;
  title: string;
  description: string;
  evidence: string | null;
  level: ProjectRiskLevel;
  status: "ACTIVE" | "RESOLVED";
  sourceType: RiskSourceType;
  sourceBatchId: string | null;
  sourceRefId: string | null;
  reporterUserId: string | null;
  reporterNameSource: string | null;
  weekCode: string | null;
  suggestion: string | null;
  detectedAt: string;
  resolvedAt: string | null;
  resolvedById: string | null;
  resolutionReason: string | null;
  dedupeFingerprint: string;
}

interface ImportedRiskResult {
  risk: Risk;
  beforeSnapshot?: Prisma.InputJsonValue;
}

@Injectable()
export class ProjectImportService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly parser: ProjectListParser,
    private readonly audit: AuditService,
    private readonly config: ConfigService,
    private readonly timeline: RiskTimelineService,
  ) {}

  async preview(
    file: Express.Multer.File,
    context: AdminRequestContext,
  ): Promise<ProjectImportBatchDetail> {
    this.validateFile(file);
    const fileName = this.safeFileName(file.originalname);
    const fileHash = createHash("sha256").update(file.buffer).digest("hex");
    const batchId = randomUUID();
    let parsed;
    try {
      parsed = await this.parser.parse(file.buffer);
    } catch (error) {
      await this.audit.record({
        actorUserId: context.identity.user.id,
        module: "IMPORT",
        action: "PROJECT_IMPORT_PREVIEW_FAILED",
        resourceType: "IMPORT_FILE",
        resourceId: fileName.slice(0, 128),
        result: "FAILURE",
        traceId: randomUUID(),
        clientIp: context.clientIp,
        userAgent: context.userAgent,
        errorCode: "INVALID_PROJECT_WORKBOOK",
      });
      throw new BadRequestException(
        error instanceof Error ? error.message : "项目清单 Excel 解析失败",
      );
    }

    const existingProjects = await this.prisma.project.findMany();
    const byImportKey = new Map(
      existingProjects
        .filter(({ importKey }) => Boolean(importKey))
        .map((project) => [project.importKey!, project]),
    );
    const byExternalCode = new Map(
      existingProjects
        .filter(({ externalCode }) => Boolean(externalCode))
        .map((project) => [
          this.normalize(project.externalCode!),
          project,
        ]),
    );
    for (const row of parsed.rows) {
      const matched =
        byImportKey.get(row.importKey) ??
        (row.externalCode
          ? byExternalCode.get(this.normalize(row.externalCode))
          : undefined);
      if (matched) {
        row.action = "UPDATE";
        row.matchedProjectId = matched.id;
      }
    }
    this.matchSupplementalRows(
      parsed.rows,
      parsed.supplementalCollections,
      existingProjects,
    );
    this.matchLegalRows(
      parsed.rows,
      parsed.legalMatters,
      existingProjects,
    );

    const counts = this.countRows(parsed.rows);
    const supplementalCounts = this.countSupplementalRows(
      parsed.supplementalCollections,
    );
    const legalCounts = this.countLegalRows(parsed.legalMatters);
    const storageKey = `${batchId}/source.xlsx`;
    const storagePath = this.storagePath(storageKey);
    await fs.mkdir(path.dirname(storagePath), { recursive: true });
    await fs.writeFile(storagePath, file.buffer, { flag: "wx" });

    let batch: BatchDetailRecord;
    try {
      batch = await this.prisma.importBatch.create({
        data: {
          id: batchId,
          fileName,
          fileHash,
          storageKey,
          sheetName: parsed.sheetName,
          sourceMeta: {
            sheetNames: parsed.sheetNames,
            ignoredSheets: parsed.ignoredSheets,
            monthAttributes: parsed.monthAttributes,
            supplementalSheet:
              parsed.supplementalCollections.length > 0
                ? "涵谷回款"
                : null,
            legalSheet:
              parsed.legalMatters.length > 0
                ? "发函-诉讼清单"
                : null,
          },
          totalRows: parsed.rows.length,
          readyRows: counts.ready,
          warningRows: counts.warning,
          errorRows: counts.error,
          supplementalTotalRows:
            parsed.supplementalCollections.length,
          supplementalMatchedRows: supplementalCounts.matched,
          supplementalUnmatchedRows: supplementalCounts.unmatched,
          supplementalAmbiguousRows: supplementalCounts.ambiguous,
          supplementalWarningRows: supplementalCounts.warning,
          supplementalErrorRows: supplementalCounts.error,
          legalTotalRows: parsed.legalMatters.length,
          legalMatchedRows: legalCounts.matched,
          legalUnmatchedRows: legalCounts.unmatched,
          legalAmbiguousRows: legalCounts.ambiguous,
          legalWarningRows: legalCounts.warning,
          legalErrorRows: legalCounts.error,
          uploadedById: context.identity.user.id,
          rows: {
            create: parsed.rows.map((row) => ({
              rowNumber: row.rowNumber,
              importKey: row.importKey,
              action: row.action,
              status: row.status,
              externalCode: row.externalCode,
              projectName: row.projectName,
              departmentName: row.departmentName,
              deliveryOwnerName: row.deliveryOwnerName,
              annualPlanAmount: this.decimal(row.annualPlanAmount),
              actualCollectedAmount: this.decimal(
                row.actualCollectedAmount,
              ),
              remainingAmount: this.decimal(row.remainingAmount),
              monthlyCollections:
                row.monthlyCollections as unknown as Prisma.InputJsonValue,
              monthAttributes:
                row.monthAttributes as Prisma.InputJsonValue,
              collectionRiskLevel: row.collectionRiskLevel,
              collectionProgress: row.collectionProgress,
              sourceSnapshot:
                row.sourceSnapshot as Prisma.InputJsonValue,
              warnings: row.warnings as Prisma.InputJsonValue,
              errors: row.errors as Prisma.InputJsonValue,
              matchedProjectId: row.matchedProjectId,
            })),
          },
          supplementalRows: {
            create: parsed.supplementalCollections.map((row) => ({
              rowNumber: row.rowNumber,
              sourceKey: row.sourceKey,
              status: row.status,
              matchStatus: row.matchStatus,
              matchedImportKey: row.matchedImportKey,
              projectId: row.matchedProjectId,
              externalCode: row.externalCode,
              projectName: row.projectName,
              contractReceivableAmount: this.decimal(
                row.contractReceivableAmount,
              ),
              procurementContractAmount: this.decimal(
                row.procurementContractAmount,
              ),
              cumulativeCollectedAmount: this.decimal(
                row.cumulativeCollectedAmount,
              ),
              remainingUncollectedAmount: this.decimal(
                row.remainingUncollectedAmount,
              ),
              actualCollectedThisYear: this.decimal(
                row.actualCollectedThisYear,
              ),
              actualCollectedNetThisYear: this.decimal(
                row.actualCollectedNetThisYear,
              ),
              annualCollectionPlan: this.decimal(
                row.annualCollectionPlan,
              ),
              collectionRiskLevel: row.collectionRiskLevel,
              monthlyCollections:
                row.monthlyCollections as unknown as Prisma.InputJsonValue,
              monthAttributes:
                row.monthAttributes as Prisma.InputJsonValue,
              afterYearAmount: this.decimal(row.afterYearAmount),
              sourceSnapshot:
                row.sourceSnapshot as Prisma.InputJsonValue,
              warnings: row.warnings as Prisma.InputJsonValue,
              errors: row.errors as Prisma.InputJsonValue,
            })),
          },
          legalRows: {
            create: parsed.legalMatters.map((row) => ({
              rowNumber: row.rowNumber,
              sourceKey: row.sourceKey,
              status: row.status,
              matchStatus: row.matchStatus,
              matchedImportKey: row.matchedImportKey,
              projectId: row.matchedProjectId,
              externalCode: row.externalCode,
              projectName: row.projectName,
              departmentName: row.departmentName,
              deliveryOwnerName: row.deliveryOwnerName,
              annualPlanAmount: this.decimal(row.annualPlanAmount),
              collectionRiskLevel: row.collectionRiskLevel,
              legalProgress: row.legalProgress,
              monthlyCollections:
                row.monthlyCollections as unknown as Prisma.InputJsonValue,
              monthAttributes:
                row.monthAttributes as Prisma.InputJsonValue,
              sourceSnapshot:
                row.sourceSnapshot as Prisma.InputJsonValue,
              warnings: row.warnings as Prisma.InputJsonValue,
              errors: row.errors as Prisma.InputJsonValue,
            })),
          },
        },
        include: BATCH_DETAIL_INCLUDE,
      });
    } catch (error) {
      await fs.rm(path.dirname(storagePath), {
        recursive: true,
        force: true,
      });
      throw error;
    }

    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "IMPORT",
      action: "PROJECT_IMPORT_PREVIEWED",
      resourceType: "IMPORT_BATCH",
      resourceId: batch.id,
      result: "SUCCESS",
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      afterSnapshot: {
        fileHash,
        totalRows: batch.totalRows,
        readyRows: batch.readyRows,
        warningRows: batch.warningRows,
        errorRows: batch.errorRows,
        supplementalTotalRows: batch.supplementalTotalRows,
        supplementalMatchedRows: batch.supplementalMatchedRows,
        supplementalUnmatchedRows: batch.supplementalUnmatchedRows,
        legalTotalRows: batch.legalTotalRows,
        legalMatchedRows: batch.legalMatchedRows,
        legalUnmatchedRows: batch.legalUnmatchedRows,
      },
    });
    return this.mapDetail(batch);
  }

  async list(
    query: ListImportBatchesQueryDto,
  ): Promise<PaginatedResponse<ProjectImportBatchSummary>> {
    const [items, total] = await this.prisma.$transaction([
      this.prisma.importBatch.findMany({
        include: BATCH_SUMMARY_INCLUDE,
        orderBy: { createdAt: "desc" },
        skip: (query.page - 1) * query.pageSize,
        take: query.pageSize,
      }),
      this.prisma.importBatch.count(),
    ]);
    return {
      items: items.map((batch) => this.mapSummary(batch)),
      page: query.page,
      pageSize: query.pageSize,
      total,
    };
  }

  async detail(id: string): Promise<ProjectImportBatchDetail> {
    return this.mapDetail(await this.getBatch(id));
  }

  async sourceFile(
    id: string,
  ): Promise<{ fileName: string; buffer: Buffer }> {
    const batch = await this.prisma.importBatch.findUnique({
      where: { id },
      select: {
        fileName: true,
        storageKey: true,
      },
    });
    if (!batch) {
      throw new NotFoundException("导入批次不存在");
    }
    try {
      return {
        fileName: batch.fileName,
        buffer: await fs.readFile(this.storagePath(batch.storageKey)),
      };
    } catch (error) {
      if (
        error &&
        typeof error === "object" &&
        "code" in error &&
        error.code === "ENOENT"
      ) {
        throw new NotFoundException("导入源文件不存在或已被清理");
      }
      throw error;
    }
  }

  async projectOptions(): Promise<ProjectOption[]> {
    const projects = await this.prisma.project.findMany({
      where: {
        status: { not: ProjectStatus.ARCHIVED },
      },
      select: {
        id: true,
        externalCode: true,
        name: true,
        department: {
          select: {
            name: true,
          },
        },
      },
      orderBy: [{ name: "asc" }],
      take: 500,
    });
    return projects.map((project) => ({
      id: project.id,
      externalCode: project.externalCode,
      name: project.name,
      departmentName: project.department?.name ?? null,
    }));
  }

  async matchSupplemental(
    rowId: string,
    dto: MatchSupplementalCollectionDto,
    context: AdminRequestContext,
  ): Promise<ProjectImportBatchDetail> {
    const row = await this.prisma.supplementalCollectionRow.findUnique({
      where: { id: rowId },
      include: {
        batch: true,
        project: {
          select: { id: true, name: true },
        },
      },
    });
    if (!row) {
      throw new NotFoundException("补充回款记录不存在");
    }
    if (
      row.batch.status !== ImportBatchStatus.IMPORTED ||
      row.status !== "IMPORTED"
    ) {
      throw new ConflictException("只有已导入批次的补充回款记录可以调整关联");
    }
    const project = await this.prisma.project.findFirst({
      where: {
        id: dto.projectId,
        status: { not: ProjectStatus.ARCHIVED },
      },
      select: {
        id: true,
        name: true,
      },
    });
    if (!project) {
      throw new NotFoundException("目标项目不存在或已归档");
    }

    await this.prisma.$transaction(async (transaction) => {
      await transaction.supplementalCollectionRow.update({
        where: { id: row.id },
        data: {
          projectId: project.id,
          matchedImportKey: null,
          matchStatus: "MATCHED",
          warnings: this.withoutSupplementalMatchWarnings(row.warnings),
        },
      });
      await this.updateSupplementalBatchCounts(transaction, row.batchId);
    });

    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "IMPORT",
      action: "SUPPLEMENTAL_COLLECTION_MATCHED",
      resourceType: "SUPPLEMENTAL_COLLECTION_ROW",
      resourceId: row.id,
      result: "SUCCESS",
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      beforeSnapshot: {
        matchStatus: row.matchStatus,
        projectId: row.projectId,
        projectName: row.project?.name ?? null,
      },
      afterSnapshot: {
        matchStatus: "MATCHED",
        projectId: project.id,
        projectName: project.name,
      },
    });
    return this.detail(row.batchId);
  }

  async unmatchSupplemental(
    rowId: string,
    context: AdminRequestContext,
  ): Promise<ProjectImportBatchDetail> {
    const row = await this.prisma.supplementalCollectionRow.findUnique({
      where: { id: rowId },
      include: {
        batch: true,
        project: {
          select: { id: true, name: true },
        },
      },
    });
    if (!row) {
      throw new NotFoundException("补充回款记录不存在");
    }
    if (
      row.batch.status !== ImportBatchStatus.IMPORTED ||
      row.status !== "IMPORTED"
    ) {
      throw new ConflictException("只有已导入批次的补充回款记录可以调整关联");
    }
    if (!row.projectId) {
      throw new ConflictException("该补充回款记录当前未关联项目");
    }

    const warnings = this.withoutSupplementalMatchWarnings(row.warnings);
    warnings.push("已由管理员解除项目关联，记录保留为待匹配");
    await this.prisma.$transaction(async (transaction) => {
      await transaction.supplementalCollectionRow.update({
        where: { id: row.id },
        data: {
          projectId: null,
          matchedImportKey: null,
          matchStatus: "UNMATCHED",
          warnings,
        },
      });
      await this.updateSupplementalBatchCounts(transaction, row.batchId);
    });

    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "IMPORT",
      action: "SUPPLEMENTAL_COLLECTION_UNMATCHED",
      resourceType: "SUPPLEMENTAL_COLLECTION_ROW",
      resourceId: row.id,
      result: "SUCCESS",
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      beforeSnapshot: {
        matchStatus: row.matchStatus,
        projectId: row.projectId,
        projectName: row.project?.name ?? null,
      },
      afterSnapshot: {
        matchStatus: "UNMATCHED",
        projectId: null,
      },
    });
    return this.detail(row.batchId);
  }

  async confirm(
    id: string,
    dto: ConfirmProjectImportDto,
    context: AdminRequestContext,
  ): Promise<ProjectImportBatchDetail> {
    const batch = await this.getBatch(id);
    if (batch.status !== ImportBatchStatus.PREVIEWED) {
      throw new ConflictException("只有预检完成的批次可以确认导入");
    }
    if (
      batch.errorRows > 0 ||
      batch.supplementalErrorRows > 0 ||
      batch.legalErrorRows > 0
    ) {
      throw new BadRequestException("批次包含错误行，请修正 Excel 后重新上传");
    }
    if (
      (batch.warningRows > 0 ||
        batch.supplementalWarningRows > 0 ||
        batch.legalWarningRows > 0) &&
      !dto.acknowledgeWarnings
    ) {
      throw new BadRequestException("请先确认批次中的警告信息");
    }

    const result = await this.prisma.$transaction(async (transaction) => {
      const departmentIds = new Map<string, string>();
      const projectsByExternalCode = new Map<string, Project>();
      const initialProjectSnapshots = new Map<
        string,
        Prisma.InputJsonValue | null
      >();
      const initialRiskSnapshots = new Map<
        string,
        Prisma.InputJsonValue | null
      >();
      let createdRows = 0;
      let updatedRows = 0;
      const importTime = new Date();
      const categories = await transaction.riskCategory.findMany({
        where: {
          code: { in: ["COLLECTION", "LITIGATION"] },
        },
        select: {
          id: true,
          code: true,
        },
      });
      const categoryIds = new Map(
        categories.map((category) => [category.code, category.id]),
      );
      const collectionCategoryId = categoryIds.get("COLLECTION");
      const litigationCategoryId = categoryIds.get("LITIGATION");
      if (!collectionCategoryId || !litigationCategoryId) {
        throw new ConflictException(
          "风险分类基础数据缺失，请先执行数据库种子初始化",
        );
      }

      for (const row of batch.rows) {
        if (!["READY", "WARNING"].includes(row.status)) {
          continue;
        }
        const departmentName = row.departmentName!;
        let departmentId = departmentIds.get(departmentName);
        if (!departmentId) {
          const code = `IMPORT_${createHash("sha256")
            .update(departmentName)
            .digest("hex")
            .slice(0, 12)
            .toUpperCase()}`;
          const department = await transaction.department.upsert({
            where: { code },
            create: {
              code,
              name: departmentName,
              enabled: true,
              sortOrder: 1000,
            },
            update: {
              name: departmentName,
              enabled: true,
            },
          });
          departmentId = department.id;
          departmentIds.set(departmentName, departmentId);
        }

        const data = this.projectData(row, departmentId, importTime);
        let project: Project;
        let beforeSnapshot: Prisma.InputJsonValue | undefined;
        const normalizedExternalCode = row.externalCode
          ? this.normalize(row.externalCode)
          : null;
        let existing = row.matchedProjectId
          ? await transaction.project.findUnique({
              where: { id: row.matchedProjectId },
            })
          : null;
        if (!existing && normalizedExternalCode) {
          existing =
            projectsByExternalCode.get(normalizedExternalCode) ??
            (await transaction.project.findUnique({
              where: { externalCode: row.externalCode! },
            }));
        }
        if (row.matchedProjectId && !existing) {
          throw new ConflictException(
            `第${row.rowNumber}行匹配的项目已不存在，请重新预检`,
          );
        }
        if (existing) {
          if (!initialProjectSnapshots.has(existing.id)) {
            initialProjectSnapshots.set(
              existing.id,
              this.snapshot(existing) as unknown as Prisma.InputJsonValue,
            );
          }
          beforeSnapshot =
            initialProjectSnapshots.get(existing.id) ?? undefined;
          project = await transaction.project.update({
            where: { id: existing.id },
            data: {
              ...data,
              importKey: existing.importKey ?? data.importKey,
              sourceVersion: { increment: 1 },
            },
          });
          updatedRows += 1;
        } else {
          project = await transaction.project.create({ data });
          initialProjectSnapshots.set(project.id, null);
          createdRows += 1;
        }
        if (normalizedExternalCode) {
          projectsByExternalCode.set(normalizedExternalCode, project);
        }

        const importedRisk =
          row.collectionRiskLevel === "UNKNOWN"
            ? null
            : await this.upsertImportedRisk(
                transaction,
                {
                projectId: project.id,
                categoryId: collectionCategoryId,
                title: `${project.name}回款风险`,
                description:
                  row.collectionProgress?.trim() ||
                  "项目清单标记存在回款风险，需持续跟踪回款进展。",
                evidence: `项目清单第${row.rowNumber}行，回款风险等级：${this.riskLevelLabel(row.collectionRiskLevel)}。`,
                level: row.collectionRiskLevel,
                sourceType: RiskSourceType.EXCEL,
                sourceBatchId: id,
                sourceRefId: row.id,
                reporterNameSource: row.deliveryOwnerName,
                weekCode: this.isoWeekCode(importTime),
                suggestion: this.collectionSuggestion(
                  row.collectionRiskLevel,
                ),
                detectedAt: importTime,
                dedupeFingerprint: this.fingerprint(
                  `PROJECT_COLLECTION:${project.id}`,
                ),
                },
                context.identity.user.id,
                context.identity.user.displayName,
              );

        let beforeRiskSnapshot = importedRisk?.beforeSnapshot;
        if (importedRisk) {
          if (!initialRiskSnapshots.has(importedRisk.risk.id)) {
            initialRiskSnapshots.set(
              importedRisk.risk.id,
              importedRisk.beforeSnapshot ?? null,
            );
          }
          beforeRiskSnapshot =
            initialRiskSnapshots.get(importedRisk.risk.id) ?? undefined;
        }

        await transaction.projectImportRow.update({
          where: { id: row.id },
          data: {
            status: "IMPORTED",
            committedProjectId: project.id,
            beforeSnapshot,
            afterSnapshot:
              this.snapshot(project) as unknown as Prisma.InputJsonValue,
            committedRiskId: importedRisk?.risk.id,
            beforeRiskSnapshot,
            afterRiskSnapshot: importedRisk
              ? (this.riskSnapshot(importedRisk.risk) as unknown as Prisma.InputJsonValue)
              : undefined,
          },
        });
      }

      for (const row of batch.supplementalRows) {
        if (!["READY", "WARNING"].includes(row.status)) {
          continue;
        }
        let projectId = row.projectId;
        if (!projectId && row.matchedImportKey) {
          const matchedProject = await transaction.project.findUnique({
            where: { importKey: row.matchedImportKey },
            select: { id: true },
          });
          projectId = matchedProject?.id ?? null;
        }
        await transaction.supplementalCollectionRow.update({
          where: { id: row.id },
          data: {
            status: "IMPORTED",
            projectId,
            matchStatus: projectId ? "MATCHED" : row.matchStatus,
          },
        });
      }

      for (const row of batch.legalRows) {
        if (!["READY", "WARNING"].includes(row.status)) {
          continue;
        }
        let projectId = row.projectId;
        if (!projectId && row.matchedImportKey) {
          const matchedProject = await transaction.project.findUnique({
            where: { importKey: row.matchedImportKey },
            select: { id: true },
          });
          projectId = matchedProject?.id ?? null;
        }
        let importedRisk: ImportedRiskResult | null = null;
        if (projectId && row.collectionRiskLevel !== "UNKNOWN") {
          const project = await transaction.project.findUnique({
            where: { id: projectId },
            select: { name: true },
          });
          if (project) {
            importedRisk = await this.upsertImportedRisk(
              transaction,
              {
              projectId,
              categoryId: litigationCategoryId,
              title: `${project.name}发函诉讼风险`,
              description:
                row.legalProgress?.trim() ||
                "发函诉讼清单标记存在法务事项，需持续跟踪处理进展。",
              evidence: `发函-诉讼清单第${row.rowNumber}行，风险等级：${this.riskLevelLabel(row.collectionRiskLevel)}。`,
              level: row.collectionRiskLevel,
              sourceType: RiskSourceType.LITIGATION,
              sourceBatchId: id,
              sourceRefId: row.id,
              reporterNameSource: row.deliveryOwnerName,
              weekCode: this.isoWeekCode(importTime),
              suggestion:
                "核实发函、协商及诉讼节点，明确责任人和下一次跟进时间。",
              detectedAt: importTime,
              dedupeFingerprint: this.fingerprint(
                `LEGAL_MATTER:${projectId}:${row.sourceKey}`,
              ),
              },
              context.identity.user.id,
              context.identity.user.displayName,
            );
          }
        }
        await transaction.legalMatterRow.update({
          where: { id: row.id },
          data: {
            status: "IMPORTED",
            projectId,
            matchStatus: projectId ? "MATCHED" : row.matchStatus,
            committedRiskId: importedRisk?.risk.id,
            beforeRiskSnapshot: importedRisk?.beforeSnapshot,
            afterRiskSnapshot: importedRisk
              ? (this.riskSnapshot(importedRisk.risk) as unknown as Prisma.InputJsonValue)
              : undefined,
          },
        });
      }

      return transaction.importBatch.update({
        where: { id },
        data: {
          status: "IMPORTED",
          createdRows,
          updatedRows,
          confirmedById: context.identity.user.id,
          confirmedAt: importTime,
        },
        include: BATCH_DETAIL_INCLUDE,
      });
    });

    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "IMPORT",
      action: "PROJECT_IMPORT_CONFIRMED",
      resourceType: "IMPORT_BATCH",
      resourceId: id,
      result: "SUCCESS",
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      afterSnapshot: {
        createdRows: result.createdRows,
        updatedRows: result.updatedRows,
        warningRows: result.warningRows,
        supplementalTotalRows: result.supplementalTotalRows,
        supplementalMatchedRows: result.supplementalMatchedRows,
        supplementalUnmatchedRows: result.supplementalUnmatchedRows,
        legalTotalRows: result.legalTotalRows,
        legalMatchedRows: result.legalMatchedRows,
        legalUnmatchedRows: result.legalUnmatchedRows,
      },
    });
    return this.mapDetail(result);
  }

  async rollback(
    id: string,
    context: AdminRequestContext,
  ): Promise<ProjectImportBatchDetail> {
    const batch = await this.getBatch(id);
    if (batch.status !== ImportBatchStatus.IMPORTED) {
      throw new ConflictException("只有已导入的批次可以回滚");
    }
    const projectIds = batch.rows
      .map(({ committedProjectId }) => committedProjectId)
      .filter((value): value is string => Boolean(value));
    const laterImport = await this.prisma.projectImportRow.findFirst({
      where: {
        committedProjectId: { in: projectIds },
        batchId: { not: id },
        batch: {
          status: "IMPORTED",
          confirmedAt: batch.confirmedAt
            ? { gt: batch.confirmedAt }
            : undefined,
        },
      },
      select: { id: true },
    });
    if (laterImport) {
      throw new ConflictException(
        "该批次涉及的项目已有后续导入，不能直接回滚",
      );
    }

    const result = await this.prisma.$transaction(async (transaction) => {
      await transaction.riskTimelineEvent.deleteMany({
        where: { sourceBatchId: id },
      });
      for (const row of batch.legalRows) {
        if (!row.committedRiskId || row.status !== "IMPORTED") {
          continue;
        }
        await this.restoreImportedRisk(
          transaction,
          row.committedRiskId,
          row.beforeRiskSnapshot,
        );
      }
      for (const row of batch.rows) {
        const projectId = row.committedProjectId;
        if (!projectId || row.status !== "IMPORTED") {
          continue;
        }
        if (row.committedRiskId) {
          await this.restoreImportedRisk(
            transaction,
            row.committedRiskId,
            row.beforeRiskSnapshot,
          );
        }
        const before = this.readSnapshot(row.beforeSnapshot);
        if (!before) {
          await transaction.project.deleteMany({
            where: { id: projectId },
          });
        } else {
          await transaction.project.update({
            where: { id: projectId },
            data: this.restoreData(before),
          });
        }
        await transaction.projectImportRow.update({
          where: { id: row.id },
          data: { status: "ROLLED_BACK" },
        });
      }
      await transaction.supplementalCollectionRow.updateMany({
        where: {
          batchId: id,
          status: "IMPORTED",
        },
        data: {
          status: "ROLLED_BACK",
        },
      });
      await transaction.legalMatterRow.updateMany({
        where: {
          batchId: id,
          status: "IMPORTED",
        },
        data: {
          status: "ROLLED_BACK",
        },
      });
      await transaction.department.deleteMany({
        where: {
          code: { startsWith: "IMPORT_" },
          projects: { none: {} },
          users: { none: {} },
        },
      });

      return transaction.importBatch.update({
        where: { id },
        data: {
          status: "ROLLED_BACK",
          rolledBackById: context.identity.user.id,
          rolledBackAt: new Date(),
        },
        include: BATCH_DETAIL_INCLUDE,
      });
    });

    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "IMPORT",
      action: "PROJECT_IMPORT_ROLLED_BACK",
      resourceType: "IMPORT_BATCH",
      resourceId: id,
      result: "SUCCESS",
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      beforeSnapshot: {
        status: "IMPORTED",
        createdRows: batch.createdRows,
        updatedRows: batch.updatedRows,
      },
      afterSnapshot: { status: "ROLLED_BACK" },
    });
    return this.mapDetail(result);
  }

  private async getBatch(id: string): Promise<BatchDetailRecord> {
    const batch = await this.prisma.importBatch.findUnique({
      where: { id },
      include: BATCH_DETAIL_INCLUDE,
    });
    if (!batch) {
      throw new NotFoundException("导入批次不存在");
    }
    return batch;
  }

  private validateFile(file: Express.Multer.File): void {
    if (!file.originalname.toLocaleLowerCase().endsWith(".xlsx")) {
      throw new BadRequestException("仅支持 .xlsx 项目清单");
    }
    if (
      file.buffer.length < 4 ||
      file.buffer.subarray(0, 2).toString("ascii") !== "PK"
    ) {
      throw new BadRequestException("文件内容不是有效的 Excel 工作簿");
    }
  }

  private safeFileName(originalName: string): string {
    const baseName = path.basename(originalName).slice(0, 255);
    if (!/[\u0080-\u00ff]/.test(baseName)) {
      return baseName;
    }
    const decoded = Buffer.from(baseName, "latin1").toString("utf8");
    return decoded.includes("\uFFFD") ? baseName : decoded.slice(0, 255);
  }

  private storagePath(storageKey: string): string {
    const configured = this.config.get<string>(
      "IMPORT_STORAGE_DIR",
      "storage/excel",
    );
    const root = path.isAbsolute(configured)
      ? configured
      : path.resolve(__dirname, "../../../..", configured);
    return path.join(root, storageKey);
  }

  private normalize(value: string): string {
    return value
      .normalize("NFKC")
      .trim()
      .replace(/\s+/g, " ")
      .toLocaleLowerCase("zh-CN");
  }

  private decimal(value: string | null): Prisma.Decimal | null {
    return value === null ? null : new Prisma.Decimal(value);
  }

  private countRows(rows: ParsedProjectImportRow[]): {
    ready: number;
    warning: number;
    error: number;
  } {
    return rows.reduce(
      (counts, row) => {
        if (row.status === "READY") counts.ready += 1;
        if (row.status === "WARNING") counts.warning += 1;
        if (row.status === "ERROR") counts.error += 1;
        return counts;
      },
      { ready: 0, warning: 0, error: 0 },
    );
  }

  private matchSupplementalRows(
    mainRows: ParsedProjectImportRow[],
    supplementalRows: ParsedSupplementalCollectionRow[],
    existingProjects: Project[],
  ): void {
    type Candidate = {
      identity: string;
      importKey?: string;
      projectId?: string;
    };
    const byCode = new Map<string, Candidate[]>();
    const byName = new Map<string, Candidate[]>();
    const addCandidate = (
      map: Map<string, Candidate[]>,
      key: string | null,
      candidate: Candidate,
    ): void => {
      if (!key) return;
      const normalizedKey = this.normalize(key);
      if (!normalizedKey) return;
      const current = map.get(normalizedKey) ?? [];
      if (!current.some(({ identity }) => identity === candidate.identity)) {
        current.push(candidate);
        map.set(normalizedKey, current);
      }
    };

    for (const row of mainRows) {
      const candidate: Candidate = row.matchedProjectId
        ? {
            identity: `PROJECT:${row.matchedProjectId}`,
            projectId: row.matchedProjectId,
            importKey: row.importKey,
          }
        : {
            identity: row.externalCode
              ? `CODE:${this.normalize(row.externalCode)}`
              : `IMPORT:${row.importKey}`,
            importKey: row.importKey,
          };
      addCandidate(byCode, row.externalCode, candidate);
      addCandidate(byName, row.projectName, candidate);
    }
    for (const project of existingProjects) {
      const candidate: Candidate = {
        identity: `PROJECT:${project.id}`,
        projectId: project.id,
        importKey: project.importKey ?? undefined,
      };
      addCandidate(byCode, project.externalCode, candidate);
      addCandidate(byName, project.name, candidate);
      addCandidate(byName, project.alias, candidate);
    }

    for (const row of supplementalRows) {
      const codeMatches = row.externalCode
        ? byCode.get(this.normalize(row.externalCode)) ?? []
        : [];
      const nameMatches = row.projectName
        ? byName.get(this.normalize(row.projectName)) ?? []
        : [];
      const matches = codeMatches.length > 0 ? codeMatches : nameMatches;
      if (matches.length === 1) {
        row.matchStatus = "MATCHED";
        row.matchedProjectId = matches[0]?.projectId;
        row.matchedImportKey = matches[0]?.importKey;
      } else if (matches.length > 1) {
        row.matchStatus = "AMBIGUOUS";
        row.warnings.push("匹配到多个主项目，需人工确认关联关系");
      } else {
        row.matchStatus = "UNMATCHED";
        row.warnings.push(
          "未找到可精确匹配的主项目，记录将保留为待匹配且不会新增项目",
        );
      }
      row.status =
        row.errors.length > 0
          ? "ERROR"
          : row.warnings.length > 0
            ? "WARNING"
            : "READY";
    }
  }

  private countSupplementalRows(
    rows: ParsedSupplementalCollectionRow[],
  ): {
    matched: number;
    unmatched: number;
    ambiguous: number;
    warning: number;
    error: number;
  } {
    return rows.reduce(
      (counts, row) => {
        if (row.matchStatus === "MATCHED") counts.matched += 1;
        if (row.matchStatus === "UNMATCHED") counts.unmatched += 1;
        if (row.matchStatus === "AMBIGUOUS") counts.ambiguous += 1;
        if (row.status === "WARNING") counts.warning += 1;
        if (row.status === "ERROR") counts.error += 1;
        return counts;
      },
      {
        matched: 0,
        unmatched: 0,
        ambiguous: 0,
        warning: 0,
        error: 0,
      },
    );
  }

  private async updateSupplementalBatchCounts(
    transaction: Prisma.TransactionClient,
    batchId: string,
  ): Promise<void> {
    const grouped = await transaction.supplementalCollectionRow.groupBy({
      by: ["matchStatus"],
      where: { batchId },
      _count: { _all: true },
    });
    const count = (status: "MATCHED" | "UNMATCHED" | "AMBIGUOUS") =>
      grouped.find((item) => item.matchStatus === status)?._count._all ?? 0;
    await transaction.importBatch.update({
      where: { id: batchId },
      data: {
        supplementalMatchedRows: count("MATCHED"),
        supplementalUnmatchedRows: count("UNMATCHED"),
        supplementalAmbiguousRows: count("AMBIGUOUS"),
      },
    });
  }

  private withoutSupplementalMatchWarnings(
    value: Prisma.JsonValue | null,
  ): string[] {
    return this.stringArray(value).filter(
      (message) =>
        message !== "匹配到多个主项目，需人工确认关联关系" &&
        message !==
          "未找到可精确匹配的主项目，记录将保留为待匹配且不会新增项目" &&
        message !== "已由管理员解除项目关联，记录保留为待匹配",
    );
  }

  private matchLegalRows(
    mainRows: ParsedProjectImportRow[],
    legalRows: ParsedLegalMatterRow[],
    existingProjects: Project[],
  ): void {
    type Candidate = {
      identity: string;
      importKey?: string;
      projectId?: string;
    };
    const byCode = new Map<string, Candidate[]>();
    const byName = new Map<string, Candidate[]>();
    const addCandidate = (
      map: Map<string, Candidate[]>,
      key: string | null,
      candidate: Candidate,
    ): void => {
      if (!key) return;
      const normalizedKey = this.normalize(key);
      if (!normalizedKey) return;
      const current = map.get(normalizedKey) ?? [];
      if (!current.some(({ identity }) => identity === candidate.identity)) {
        current.push(candidate);
        map.set(normalizedKey, current);
      }
    };

    for (const row of mainRows) {
      const candidate: Candidate = row.matchedProjectId
        ? {
            identity: `PROJECT:${row.matchedProjectId}`,
            projectId: row.matchedProjectId,
            importKey: row.importKey,
          }
        : {
            identity: row.externalCode
              ? `CODE:${this.normalize(row.externalCode)}`
              : `IMPORT:${row.importKey}`,
            importKey: row.importKey,
          };
      addCandidate(byCode, row.externalCode, candidate);
      addCandidate(byName, row.projectName, candidate);
    }
    for (const project of existingProjects) {
      const candidate: Candidate = {
        identity: `PROJECT:${project.id}`,
        projectId: project.id,
        importKey: project.importKey ?? undefined,
      };
      addCandidate(byCode, project.externalCode, candidate);
      addCandidate(byName, project.name, candidate);
      addCandidate(byName, project.alias, candidate);
    }

    for (const row of legalRows) {
      const codeMatches = row.externalCode
        ? byCode.get(this.normalize(row.externalCode)) ?? []
        : [];
      const nameMatches = row.projectName
        ? byName.get(this.normalize(row.projectName)) ?? []
        : [];
      const matches = codeMatches.length > 0 ? codeMatches : nameMatches;
      if (matches.length === 1) {
        row.matchStatus = "MATCHED";
        row.matchedProjectId = matches[0]?.projectId;
        row.matchedImportKey = matches[0]?.importKey;
      } else if (matches.length > 1) {
        row.matchStatus = "AMBIGUOUS";
        row.warnings.push("匹配到多个主项目，需人工确认关联关系");
      } else {
        row.matchStatus = "UNMATCHED";
        row.warnings.push(
          "未找到可精确匹配的主项目，法务事项将保留为待匹配且不会新增项目",
        );
      }
      row.status =
        row.errors.length > 0
          ? "ERROR"
          : row.warnings.length > 0
            ? "WARNING"
            : "READY";
    }
  }

  private countLegalRows(rows: ParsedLegalMatterRow[]): {
    matched: number;
    unmatched: number;
    ambiguous: number;
    warning: number;
    error: number;
  } {
    return rows.reduce(
      (counts, row) => {
        if (row.matchStatus === "MATCHED") counts.matched += 1;
        if (row.matchStatus === "UNMATCHED") counts.unmatched += 1;
        if (row.matchStatus === "AMBIGUOUS") counts.ambiguous += 1;
        if (row.status === "WARNING") counts.warning += 1;
        if (row.status === "ERROR") counts.error += 1;
        return counts;
      },
      {
        matched: 0,
        unmatched: 0,
        ambiguous: 0,
        warning: 0,
        error: 0,
      },
    );
  }

  private projectData(
    row: BatchDetailRecord["rows"][number],
    departmentId: string,
    importedAt: Date,
  ): Prisma.ProjectUncheckedCreateInput {
    return {
      externalCode: row.externalCode,
      importKey: row.importKey,
      name: row.projectName!,
      departmentId,
      deliveryOwnerName: row.deliveryOwnerName,
      annualPlanAmount: row.annualPlanAmount,
      actualCollectedAmount: row.actualCollectedAmount,
      remainingAmount: row.remainingAmount,
      monthlyCollections:
        (row.monthlyCollections as Prisma.InputJsonValue | null) ??
        Prisma.JsonNull,
      monthAttributes:
        (row.monthAttributes as Prisma.InputJsonValue | null) ??
        Prisma.JsonNull,
      collectionRiskLevel: row.collectionRiskLevel,
      collectionProgress: row.collectionProgress,
      lastImportedAt: importedAt,
    };
  }

  private snapshot(project: Project): ProjectSnapshot {
    return {
      externalCode: project.externalCode,
      importKey: project.importKey,
      name: project.name,
      alias: project.alias,
      status: project.status,
      departmentId: project.departmentId,
      managerId: project.managerId,
      deliveryOwnerName: project.deliveryOwnerName,
      annualPlanAmount: project.annualPlanAmount?.toFixed(2) ?? null,
      actualCollectedAmount:
        project.actualCollectedAmount?.toFixed(2) ?? null,
      remainingAmount: project.remainingAmount?.toFixed(2) ?? null,
      monthlyCollections: project.monthlyCollections,
      monthAttributes: project.monthAttributes,
      collectionRiskLevel: project.collectionRiskLevel,
      collectionProgress: project.collectionProgress,
      lastImportedAt: project.lastImportedAt?.toISOString() ?? null,
      sourceVersion: project.sourceVersion,
    };
  }

  private readSnapshot(value: Prisma.JsonValue | null): ProjectSnapshot | null {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    return value as unknown as ProjectSnapshot;
  }

  private restoreData(snapshot: ProjectSnapshot): Prisma.ProjectUncheckedUpdateInput {
    return {
      externalCode: snapshot.externalCode,
      importKey: snapshot.importKey,
      name: snapshot.name,
      alias: snapshot.alias,
      status: snapshot.status,
      departmentId: snapshot.departmentId,
      managerId: snapshot.managerId,
      deliveryOwnerName: snapshot.deliveryOwnerName,
      annualPlanAmount: this.decimal(snapshot.annualPlanAmount),
      actualCollectedAmount: this.decimal(
        snapshot.actualCollectedAmount,
      ),
      remainingAmount: this.decimal(snapshot.remainingAmount),
      monthlyCollections:
        (snapshot.monthlyCollections as Prisma.InputJsonValue | null) ??
        Prisma.JsonNull,
      monthAttributes:
        (snapshot.monthAttributes as Prisma.InputJsonValue | null) ??
        Prisma.JsonNull,
      collectionRiskLevel: snapshot.collectionRiskLevel,
      collectionProgress: snapshot.collectionProgress,
      lastImportedAt: snapshot.lastImportedAt
        ? new Date(snapshot.lastImportedAt)
        : null,
      sourceVersion: snapshot.sourceVersion,
    };
  }

  private async upsertImportedRisk(
    transaction: Prisma.TransactionClient,
    data: Prisma.RiskUncheckedCreateInput,
    actorUserId: string,
    actorName: string,
  ): Promise<ImportedRiskResult> {
    const existing = await transaction.risk.findUnique({
      where: {
        dedupeFingerprint: data.dedupeFingerprint,
      },
    });
    const beforeSnapshot = existing
      ? (this.riskSnapshot(existing) as unknown as Prisma.InputJsonValue)
      : undefined;
    const risk = existing
      ? await transaction.risk.update({
          where: { id: existing.id },
          data: {
            ...data,
            status: "ACTIVE",
            resolvedAt: null,
            resolvedById: null,
            resolutionReason: null,
          },
        })
      : await transaction.risk.create({ data });
    const action = await this.upsertActionItemForRisk(
      transaction,
      risk,
    );
    if (!existing) {
      await this.timeline.record(transaction, {
        projectId: risk.projectId,
        riskId: risk.id,
        eventType: "RISK_CREATED",
        title: "风险首次识别",
        description: risk.description,
        actorUserId,
        actorNameSource: actorName,
        sourceBatchId: risk.sourceBatchId,
        occurredAt: risk.detectedAt,
        metadata: {
          level: risk.level,
          sourceType: risk.sourceType,
        },
      });
    } else {
      if (existing.status === "RESOLVED") {
        await this.timeline.record(transaction, {
          projectId: risk.projectId,
          riskId: risk.id,
          eventType: "RISK_REOPENED",
          title: "风险重新进入跟踪",
          description: "最新导入数据再次识别到该风险，风险状态已恢复为有效。",
          fromValue: existing.status,
          toValue: risk.status,
          actorUserId,
          actorNameSource: actorName,
          sourceBatchId: risk.sourceBatchId,
          occurredAt: risk.updatedAt,
        });
      }
      if (existing.level !== risk.level) {
        await this.timeline.record(transaction, {
          projectId: risk.projectId,
          riskId: risk.id,
          eventType: "LEVEL_CHANGED",
          title: "风险等级发生变化",
          description: `风险等级由“${this.riskLevelLabel(existing.level)}”调整为“${this.riskLevelLabel(risk.level)}”。`,
          fromValue: existing.level,
          toValue: risk.level,
          actorUserId,
          actorNameSource: actorName,
          sourceBatchId: risk.sourceBatchId,
          occurredAt: risk.updatedAt,
        });
      }
      if (
        existing.title !== risk.title ||
        existing.description !== risk.description ||
        existing.evidence !== risk.evidence ||
        existing.suggestion !== risk.suggestion
      ) {
        await this.timeline.record(transaction, {
          projectId: risk.projectId,
          riskId: risk.id,
          eventType: "RISK_UPDATED",
          title: "风险信息已更新",
          description: "风险描述、证据或建议措施已根据最新来源更新。",
          actorUserId,
          actorNameSource: actorName,
          sourceBatchId: risk.sourceBatchId,
          occurredAt: risk.updatedAt,
        });
      }
    }
    if (action.created) {
      await this.timeline.record(transaction, {
        projectId: risk.projectId,
        riskId: risk.id,
        actionItemId: action.item.id,
        eventType: "ACTION_CREATED",
        title: "风险处理待办已生成",
        description: action.item.description,
        actorUserId,
        actorNameSource: actorName,
        sourceBatchId: risk.sourceBatchId,
        occurredAt: action.item.createdAt,
        metadata: {
          urgency: action.item.urgency,
          assigneeName: action.item.assigneeNameSource,
        },
      });
    }
    return { risk, beforeSnapshot };
  }

  private async restoreImportedRisk(
    transaction: Prisma.TransactionClient,
    riskId: string,
    snapshotValue: Prisma.JsonValue | null,
  ): Promise<void> {
    const snapshot = this.readRiskSnapshot(snapshotValue);
    if (!snapshot) {
      await transaction.risk.deleteMany({ where: { id: riskId } });
      return;
    }
    const restoredRisk = await transaction.risk.update({
      where: { id: riskId },
      data: {
        projectId: snapshot.projectId,
        categoryId: snapshot.categoryId,
        title: snapshot.title,
        description: snapshot.description,
        evidence: snapshot.evidence,
        level: snapshot.level,
        status: snapshot.status,
        sourceType: snapshot.sourceType,
        sourceBatchId: snapshot.sourceBatchId,
        sourceRefId: snapshot.sourceRefId,
        reporterUserId: snapshot.reporterUserId,
        reporterNameSource: snapshot.reporterNameSource,
        weekCode: snapshot.weekCode,
        suggestion: snapshot.suggestion,
        detectedAt: new Date(snapshot.detectedAt),
        resolvedAt: snapshot.resolvedAt
          ? new Date(snapshot.resolvedAt)
          : null,
        resolvedById: snapshot.resolvedById,
        resolutionReason: snapshot.resolutionReason,
        dedupeFingerprint: snapshot.dedupeFingerprint,
      },
    });
    if (restoredRisk.status === "ACTIVE") {
      await this.upsertActionItemForRisk(transaction, restoredRisk);
    } else {
      await transaction.actionItem.deleteMany({
        where: { riskId: restoredRisk.id },
      });
    }
  }

  private async upsertActionItemForRisk(
    transaction: Prisma.TransactionClient,
    risk: Risk,
  ): Promise<{ item: ActionItem; created: boolean }> {
    const title = `${risk.title}处理事项`.slice(0, 250);
    const description = risk.suggestion?.trim() || risk.description;
    const existing = await transaction.actionItem.findUnique({
      where: { riskId: risk.id },
      select: { id: true },
    });
    const item = await transaction.actionItem.upsert({
      where: { riskId: risk.id },
      create: {
        riskId: risk.id,
        projectId: risk.projectId,
        title,
        description,
        urgency: urgencyForRisk(risk.level),
        status: "PENDING",
        sourceType: "RISK_SUGGESTION",
        assigneeNameSource: defaultAssigneeForRisk(
          risk.level,
          risk.reporterNameSource,
        ),
      },
      update: {
        projectId: risk.projectId,
        title,
        description,
        urgency: urgencyForRisk(risk.level),
        sourceType: "RISK_SUGGESTION",
      },
    });
    return { item, created: !existing };
  }

  private riskSnapshot(risk: Risk): RiskSnapshot {
    return {
      projectId: risk.projectId,
      categoryId: risk.categoryId,
      title: risk.title,
      description: risk.description,
      evidence: risk.evidence,
      level: risk.level,
      status: risk.status,
      sourceType: risk.sourceType,
      sourceBatchId: risk.sourceBatchId,
      sourceRefId: risk.sourceRefId,
      reporterUserId: risk.reporterUserId,
      reporterNameSource: risk.reporterNameSource,
      weekCode: risk.weekCode,
      suggestion: risk.suggestion,
      detectedAt: risk.detectedAt.toISOString(),
      resolvedAt: risk.resolvedAt?.toISOString() ?? null,
      resolvedById: risk.resolvedById,
      resolutionReason: risk.resolutionReason,
      dedupeFingerprint: risk.dedupeFingerprint,
    };
  }

  private readRiskSnapshot(
    value: Prisma.JsonValue | null,
  ): RiskSnapshot | null {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    return value as unknown as RiskSnapshot;
  }

  private fingerprint(value: string): string {
    return createHash("sha256").update(value).digest("hex");
  }

  private isoWeekCode(date: Date): string {
    const utc = new Date(
      Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()),
    );
    const day = utc.getUTCDay() || 7;
    utc.setUTCDate(utc.getUTCDate() + 4 - day);
    const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
    const week = Math.ceil(
      ((utc.getTime() - yearStart.getTime()) / 86_400_000 + 1) / 7,
    );
    return `${utc.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
  }

  private riskLevelLabel(level: ProjectRiskLevel): string {
    return (
      {
        HIGH: "高风险",
        MEDIUM: "中风险",
        LOW: "低风险",
        UNKNOWN: "待确认",
      } as const
    )[level];
  }

  private collectionSuggestion(level: ProjectRiskLevel): string {
    if (level === "HIGH") {
      return "优先核实阻塞原因，明确责任人、回款动作和最晚完成时间。";
    }
    if (level === "MEDIUM") {
      return "纳入本周跟踪计划，确认下一笔回款节点并保留沟通证据。";
    }
    return "按既定回款计划持续跟踪，出现偏差时及时升级风险。";
  }

  private mapSummary(batch: BatchSummaryRecord): ProjectImportBatchSummary {
    return {
      id: batch.id,
      fileName: batch.fileName,
      fileHash: batch.fileHash,
      status: batch.status,
      sheetName: batch.sheetName,
      totalRows: batch.totalRows,
      readyRows: batch.readyRows,
      warningRows: batch.warningRows,
      errorRows: batch.errorRows,
      createdRows: batch.createdRows,
      updatedRows: batch.updatedRows,
      supplementalTotalRows: batch.supplementalTotalRows,
      supplementalMatchedRows: batch.supplementalMatchedRows,
      supplementalUnmatchedRows: batch.supplementalUnmatchedRows,
      supplementalAmbiguousRows: batch.supplementalAmbiguousRows,
      supplementalWarningRows: batch.supplementalWarningRows,
      supplementalErrorRows: batch.supplementalErrorRows,
      legalTotalRows: batch.legalTotalRows,
      legalMatchedRows: batch.legalMatchedRows,
      legalUnmatchedRows: batch.legalUnmatchedRows,
      legalAmbiguousRows: batch.legalAmbiguousRows,
      legalWarningRows: batch.legalWarningRows,
      legalErrorRows: batch.legalErrorRows,
      uploadedByName: batch.uploadedBy.displayName,
      createdAt: batch.createdAt.toISOString(),
      confirmedAt: batch.confirmedAt?.toISOString() ?? null,
      rolledBackAt: batch.rolledBackAt?.toISOString() ?? null,
    };
  }

  private mapDetail(batch: BatchDetailRecord): ProjectImportBatchDetail {
    const sourceMeta = this.objectValue(batch.sourceMeta);
    return {
      ...this.mapSummary(batch),
      sourceMeta: {
        sheetNames: this.stringArray(sourceMeta.sheetNames),
        ignoredSheets: this.stringArray(sourceMeta.ignoredSheets),
        monthAttributes: this.stringRecord(
          sourceMeta.monthAttributes,
        ),
      },
      rows: batch.rows.map((row) => this.mapRow(row)),
      supplementalRows: batch.supplementalRows.map((row) =>
        this.mapSupplementalRow(row),
      ),
      legalRows: batch.legalRows.map((row) =>
        this.mapLegalRow(row),
      ),
    };
  }

  private mapRow(
    row: BatchDetailRecord["rows"][number],
  ): ProjectImportRowItem {
    return {
      id: row.id,
      rowNumber: row.rowNumber,
      action: row.action,
      status: row.status as ImportRowStatus,
      externalCode: row.externalCode,
      projectName: row.projectName,
      departmentName: row.departmentName,
      deliveryOwnerName: row.deliveryOwnerName,
      annualPlanAmount: row.annualPlanAmount?.toFixed(2) ?? null,
      actualCollectedAmount:
        row.actualCollectedAmount?.toFixed(2) ?? null,
      remainingAmount: row.remainingAmount?.toFixed(2) ?? null,
      collectionRiskLevel: row.collectionRiskLevel,
      collectionProgress: row.collectionProgress,
      warnings: this.stringArray(row.warnings),
      errors: this.stringArray(row.errors),
      matchedProjectId: row.matchedProjectId,
      committedProjectId: row.committedProjectId,
    };
  }

  private mapSupplementalRow(
    row: BatchDetailRecord["supplementalRows"][number],
  ): SupplementalCollectionRowItem {
    return {
      id: row.id,
      rowNumber: row.rowNumber,
      status: row.status as ImportRowStatus,
      matchStatus: row.matchStatus,
      projectId: row.projectId,
      matchedProject: row.project
        ? {
            id: row.project.id,
            externalCode: row.project.externalCode,
            name: row.project.name,
            departmentName: row.project.department?.name ?? null,
          }
        : null,
      externalCode: row.externalCode,
      projectName: row.projectName,
      contractReceivableAmount:
        row.contractReceivableAmount?.toFixed(2) ?? null,
      procurementContractAmount:
        row.procurementContractAmount?.toFixed(2) ?? null,
      cumulativeCollectedAmount:
        row.cumulativeCollectedAmount?.toFixed(2) ?? null,
      remainingUncollectedAmount:
        row.remainingUncollectedAmount?.toFixed(2) ?? null,
      actualCollectedThisYear:
        row.actualCollectedThisYear?.toFixed(2) ?? null,
      actualCollectedNetThisYear:
        row.actualCollectedNetThisYear?.toFixed(2) ?? null,
      annualCollectionPlan:
        row.annualCollectionPlan?.toFixed(2) ?? null,
      collectionRiskLevel: row.collectionRiskLevel,
      afterYearAmount: row.afterYearAmount?.toFixed(2) ?? null,
      warnings: this.stringArray(row.warnings),
      errors: this.stringArray(row.errors),
    };
  }

  private mapLegalRow(
    row: BatchDetailRecord["legalRows"][number],
  ): LegalMatterRowItem {
    return {
      id: row.id,
      rowNumber: row.rowNumber,
      status: row.status as ImportRowStatus,
      matchStatus: row.matchStatus,
      projectId: row.projectId,
      externalCode: row.externalCode,
      projectName: row.projectName,
      departmentName: row.departmentName,
      deliveryOwnerName: row.deliveryOwnerName,
      annualPlanAmount: row.annualPlanAmount?.toFixed(2) ?? null,
      collectionRiskLevel: row.collectionRiskLevel,
      legalProgress: row.legalProgress,
      warnings: this.stringArray(row.warnings),
      errors: this.stringArray(row.errors),
    };
  }

  private objectValue(value: Prisma.JsonValue | null): Prisma.JsonObject {
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  }

  private stringArray(value: Prisma.JsonValue | undefined | null): string[] {
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string")
      : [];
  }

  private stringRecord(
    value: Prisma.JsonValue | undefined | null,
  ): Record<string, string | null> {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        typeof item === "string" ? item : null,
      ]),
    );
  }
}
