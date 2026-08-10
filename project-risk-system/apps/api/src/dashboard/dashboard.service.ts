import { Injectable, NotFoundException } from "@nestjs/common";
import {
  DataScopeType,
  ImportBatchStatus,
  ImportRowStatus,
  Prisma,
  ProjectRiskLevel,
  ProjectStatus,
  RiskSourceType,
  RiskStatus,
  RiskTimelineEventType,
  SupplementalMatchStatus,
} from "@prisma/client";

import type {
  DepartmentCollectionDetail,
  DepartmentCollectionProjectItem,
  DepartmentCollectionSummary,
  DepartmentCollectionSummaryItem,
  DepartmentCollectionTotals,
  DashboardFocusItem,
  DashboardRiskDetail,
  DashboardRiskFilterOptions,
  DashboardRiskListItem,
  DashboardRiskListResponse,
  DashboardSummary,
  RiskCollectionDetail,
  RiskCollectionListResponse,
  RiskCollectionProjectItem,
  ResolvedRiskListItem,
  ResolvedRiskListResponse,
  RiskTimelineDetail,
  RiskTimelineItem,
  RiskTimelineListResponse,
} from "@risk-platform/contracts";

import type { SessionIdentity } from "../auth/auth.types";
import { PrismaService } from "../prisma/prisma.service";
import { DataScopeService } from "../rbac/data-scope.service";
import {
  collectionCompletionRate,
  type MonthlyCollectionAmount,
  resolveNextCollection,
  resolveProjectCollectionAmounts,
} from "./collection-calculator";
import type {
  ListRiskCollectionsQueryDto,
  ListResolvedRisksQueryDto,
  ListRiskTimelineQueryDto,
  ListRisksQueryDto,
} from "./dto/dashboard-query.dto";
import { eventPresentation } from "../risk-timeline/risk-timeline-policy";

const RISK_INCLUDE = {
  category: {
    select: {
      id: true,
      code: true,
      name: true,
    },
  },
  reporterUser: {
    select: {
      displayName: true,
    },
  },
  resolvedBy: {
    select: {
      displayName: true,
    },
  },
  project: {
    select: {
      id: true,
      externalCode: true,
      name: true,
      deliveryOwnerName: true,
      actualCollectedAmount: true,
      remainingAmount: true,
      department: {
        select: {
          name: true,
        },
      },
    },
  },
} satisfies Prisma.RiskInclude;

type RiskRecord = Prisma.RiskGetPayload<{
  include: typeof RISK_INCLUDE;
}>;

const TIMELINE_INCLUDE = {
  actor: {
    select: {
      displayName: true,
    },
  },
  project: {
    select: {
      id: true,
      name: true,
      deliveryOwnerName: true,
      department: {
        select: {
          name: true,
        },
      },
    },
  },
  risk: {
    include: {
      category: {
        select: {
          name: true,
        },
      },
    },
  },
  actionItem: {
    include: {
      assigneeUser: {
        select: {
          displayName: true,
        },
      },
    },
  },
} satisfies Prisma.RiskTimelineEventInclude;

type TimelineRecord = Prisma.RiskTimelineEventGetPayload<{
  include: typeof TIMELINE_INCLUDE;
}>;

const COLLECTION_PROJECT_SELECT = {
  id: true,
  externalCode: true,
  name: true,
  deliveryOwnerName: true,
  annualPlanAmount: true,
  actualCollectedAmount: true,
  remainingAmount: true,
  monthlyCollections: true,
  monthAttributes: true,
  collectionRiskLevel: true,
  collectionProgress: true,
  lastImportedAt: true,
  department: {
    select: {
      id: true,
      name: true,
    },
  },
  supplementalCollectionRows: {
    where: {
      status: ImportRowStatus.IMPORTED,
      matchStatus: SupplementalMatchStatus.MATCHED,
      batch: {
        status: ImportBatchStatus.IMPORTED,
      },
    },
    select: {
      contractReceivableAmount: true,
      cumulativeCollectedAmount: true,
      remainingUncollectedAmount: true,
      monthlyCollections: true,
      monthAttributes: true,
      updatedAt: true,
    },
  },
  risks: {
    where: {
      status: RiskStatus.ACTIVE,
    },
    select: {
      id: true,
      title: true,
      description: true,
      level: true,
      sourceType: true,
      detectedAt: true,
      updatedAt: true,
      category: {
        select: {
          name: true,
        },
      },
    },
    orderBy: [{ level: "asc" }, { detectedAt: "desc" }],
  },
} satisfies Prisma.ProjectSelect;

type CollectionProjectRecord = Prisma.ProjectGetPayload<{
  select: typeof COLLECTION_PROJECT_SELECT;
}>;

interface CollectionProjectView {
  record: CollectionProjectRecord;
  item: DepartmentCollectionProjectItem;
  receivable: Prisma.Decimal | null;
  collected: Prisma.Decimal | null;
  remaining: Prisma.Decimal | null;
  complete: boolean;
  updatedAt: Date | null;
}

interface RiskCollectionProjectView extends CollectionProjectView {
  item: RiskCollectionProjectItem;
  monthlyCollections: MonthlyCollectionAmount[];
}

@Injectable()
export class DashboardService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly dataScopes: DataScopeService,
  ) {}

  async summary(identity: SessionIdentity): Promise<DashboardSummary> {
    const projectScope = this.projectScope(identity);
    const activeRiskWhere = this.activeRiskWhere(projectScope);
    const now = new Date();
    const weekStart = new Date(now);
    weekStart.setHours(0, 0, 0, 0);
    weekStart.setDate(weekStart.getDate() - ((weekStart.getDay() + 6) % 7));
    const riskProjectWhere: Prisma.ProjectWhereInput = {
      AND: [
        projectScope,
        {
          risks: {
            some: {
              status: RiskStatus.ACTIVE,
            },
          },
        },
      ],
    };
    const [
      projectTotal,
      deliveryProjectTotal,
      deliveryDepartments,
      activeRisks,
      riskProjectTotal,
      completeAmountProjectTotal,
      amounts,
      latestRisk,
      latestProject,
      latestImportBatch,
    ] = await this.prisma.$transaction([
      this.prisma.project.count({ where: projectScope }),
      this.prisma.project.count({
        where: {
          AND: [
            projectScope,
            {
              status: ProjectStatus.DELIVERY,
            },
          ],
        },
      }),
      this.prisma.project.findMany({
        where: {
          AND: [
            projectScope,
            {
              status: ProjectStatus.DELIVERY,
              departmentId: { not: null },
            },
          ],
        },
        distinct: ["departmentId"],
        select: { departmentId: true },
      }),
      this.prisma.risk.findMany({
        where: activeRiskWhere,
        select: {
          projectId: true,
          level: true,
          sourceType: true,
          detectedAt: true,
          title: true,
          suggestion: true,
          project: {
            select: { name: true },
          },
        },
        orderBy: [{ level: "asc" }, { updatedAt: "desc" }],
      }),
      this.prisma.project.count({ where: riskProjectWhere }),
      this.prisma.project.count({
        where: {
          AND: [
            riskProjectWhere,
            {
              actualCollectedAmount: { not: null },
              remainingAmount: { not: null },
            },
          ],
        },
      }),
      this.prisma.project.aggregate({
        where: riskProjectWhere,
        _sum: {
          actualCollectedAmount: true,
          remainingAmount: true,
        },
      }),
      this.prisma.risk.findFirst({
        where: activeRiskWhere,
        orderBy: { updatedAt: "desc" },
        select: { updatedAt: true },
      }),
      this.prisma.project.findFirst({
        where: projectScope,
        orderBy: { lastImportedAt: "desc" },
        select: { lastImportedAt: true },
      }),
      this.prisma.importBatch.findFirst({
        where: { status: ImportBatchStatus.IMPORTED },
        orderBy: [{ confirmedAt: "desc" }, { createdAt: "desc" }],
        select: {
          id: true,
          createdAt: true,
          confirmedAt: true,
        },
      }),
    ]);
    const activeRiskTotal = activeRisks.length;
    const highRisks = activeRisks.filter(
      (risk) => risk.level === ProjectRiskLevel.HIGH,
    );
    const highRiskTotal = highRisks.length;
    const mediumRiskTotal = activeRisks.filter(
      (risk) => risk.level === ProjectRiskLevel.MEDIUM,
    ).length;
    const lowRiskTotal = activeRisks.filter(
      (risk) => risk.level === ProjectRiskLevel.LOW,
    ).length;
    const unknownRiskTotal = activeRisks.filter(
      (risk) => risk.level === ProjectRiskLevel.UNKNOWN,
    ).length;
    const sourceTotal = (sourceType: RiskSourceType): number =>
      activeRisks.filter((risk) => risk.sourceType === sourceType).length;
    const highRiskFocusProjectNames = Array.from(
      new Set(highRisks.map((risk) => risk.project.name)),
    ).slice(0, 2);
    const highRiskPriorityItems = Array.from(
      new Set(
        highRisks.map(
          (risk) => risk.suggestion?.trim() || risk.title.trim(),
        ),
      ),
    ).slice(0, 3);
    let latestImportBatchCode: string | null = null;
    let latestImportCreatedProjectTotal = 0;
    if (latestImportBatch) {
      const importedAt =
        latestImportBatch.confirmedAt ?? latestImportBatch.createdAt;
      const dayStart = new Date(importedAt);
      dayStart.setHours(0, 0, 0, 0);
      const dayEnd = new Date(dayStart);
      dayEnd.setDate(dayEnd.getDate() + 1);
      const [dailyBatchSequence, createdProjectRows] =
        await this.prisma.$transaction([
          this.prisma.importBatch.count({
            where: {
              status: ImportBatchStatus.IMPORTED,
              confirmedAt: {
                gte: dayStart,
                lt: dayEnd,
                lte: importedAt,
              },
            },
          }),
          this.prisma.projectImportRow.findMany({
            where: {
              batchId: latestImportBatch.id,
              status: ImportRowStatus.IMPORTED,
              action: "CREATE",
              committedProject: { is: projectScope },
            },
            select: { committedProjectId: true },
          }),
        ]);
      const dateCode = [
        importedAt.getFullYear(),
        String(importedAt.getMonth() + 1).padStart(2, "0"),
        String(importedAt.getDate()).padStart(2, "0"),
      ].join("");
      latestImportBatchCode = `IMP-${dateCode}-${String(
        Math.max(dailyBatchSequence, 1),
      ).padStart(2, "0")}`;
      latestImportCreatedProjectTotal = new Set(
        createdProjectRows
          .map((row) => row.committedProjectId)
          .filter((projectId): projectId is string => Boolean(projectId)),
      ).size;
    }
    const collected = amounts._sum.actualCollectedAmount;
    const remaining = amounts._sum.remainingAmount;
    const totalAmount =
      collected && remaining ? collected.add(remaining) : null;
    const completionRate =
      collected && totalAmount && totalAmount.greaterThan(0)
        ? Number(collected.div(totalAmount).mul(100).toFixed(1))
        : null;
    const latestTimestamp = [
      latestRisk?.updatedAt,
      latestProject?.lastImportedAt,
    ]
      .filter((value): value is Date => Boolean(value))
      .sort((left, right) => right.getTime() - left.getTime())[0];

    return {
      projectTotal,
      deliveryProjectTotal,
      deliveryDepartmentTotal: deliveryDepartments.length,
      latestImportBatchCode,
      latestImportCreatedProjectTotal,
      activeRiskTotal,
      highRiskTotal,
      mediumRiskTotal,
      lowRiskTotal,
      unknownRiskTotal,
      riskProjectTotal,
      highRiskProjectTotal: new Set(
        highRisks.map((risk) => risk.projectId),
      ).size,
      weeklyNewRiskTotal: activeRisks.filter(
        (risk) => risk.detectedAt >= weekStart,
      ).length,
      weeklyNewHighRiskTotal: highRisks.filter(
        (risk) => risk.detectedAt >= weekStart,
      ).length,
      mailAiRiskTotal: sourceTotal(RiskSourceType.MAIL_AI),
      manualRiskTotal: sourceTotal(RiskSourceType.MANUAL),
      excelRiskTotal: sourceTotal(RiskSourceType.EXCEL),
      litigationRiskTotal: sourceTotal(RiskSourceType.LITIGATION),
      highRiskFocusProjectNames,
      highRiskPriorityItems,
      riskRemainingAmountYuan: remaining?.toFixed(2) ?? null,
      riskCollectedAmountYuan: collected?.toFixed(2) ?? null,
      riskAmountCompleteProjectTotal: completeAmountProjectTotal,
      riskAmountMissingProjectTotal:
        riskProjectTotal - completeAmountProjectTotal,
      riskCollectionCompletionRate: completionRate,
      updatedAt: latestTimestamp?.toISOString() ?? null,
      dataScope: identity.user.dataScope,
    };
  }

  async departmentCollections(
    identity: SessionIdentity,
  ): Promise<DepartmentCollectionSummary> {
    const canManageImports = identity.user.permissions.includes(
      "admin.import.manage",
    );
    const [records, pending] = await Promise.all([
      this.prisma.project.findMany({
        where: this.projectScope(identity),
        select: COLLECTION_PROJECT_SELECT,
        orderBy: [{ department: { name: "asc" } }, { name: "asc" }],
      }),
      canManageImports
        ? this.prisma.supplementalCollectionRow.aggregate({
            where: {
              status: ImportRowStatus.IMPORTED,
              matchStatus: {
                in: [
                  SupplementalMatchStatus.UNMATCHED,
                  SupplementalMatchStatus.AMBIGUOUS,
                ],
              },
              batch: {
                status: ImportBatchStatus.IMPORTED,
              },
            },
            _count: { _all: true },
            _sum: { contractReceivableAmount: true },
          })
        : Promise.resolve(null),
    ]);
    const projects = records.map((record) =>
      this.mapCollectionProject(record),
    );
    const grouped = new Map<string, CollectionProjectView[]>();
    for (const project of projects) {
      const key = project.record.department?.id ?? "unassigned";
      const current = grouped.get(key) ?? [];
      current.push(project);
      grouped.set(key, current);
    }
    const items: DepartmentCollectionSummaryItem[] = Array.from(
      grouped.entries(),
    ).map(([departmentKey, departmentProjects]) => ({
      departmentId:
        departmentProjects[0]?.record.department?.id ?? null,
      departmentKey,
      departmentName:
        departmentProjects[0]?.record.department?.name ?? "未分配部门",
      ...this.collectionTotals(departmentProjects),
    }));
    items.sort((left, right) => {
      const leftRemaining = Number(left.remainingAmountYuan ?? -1);
      const rightRemaining = Number(right.remainingAmountYuan ?? -1);
      if (leftRemaining !== rightRemaining) {
        return rightRemaining - leftRemaining;
      }
      return left.departmentName.localeCompare(
        right.departmentName,
        "zh-CN",
      );
    });

    return {
      items,
      totals: this.collectionTotals(projects),
      pendingSupplementalCount: pending?._count._all ?? null,
      pendingSupplementalReceivableAmountYuan:
        pending?._sum.contractReceivableAmount?.toFixed(2) ?? null,
      updatedAt: this.latestCollectionTimestamp(projects),
      dataScope: identity.user.dataScope,
    };
  }

  async departmentCollectionDetail(
    identity: SessionIdentity,
    departmentKey: string,
  ): Promise<DepartmentCollectionDetail> {
    const departmentWhere: Prisma.ProjectWhereInput =
      departmentKey === "unassigned"
        ? { departmentId: null }
        : { departmentId: departmentKey };
    const records = await this.prisma.project.findMany({
      where: {
        AND: [this.projectScope(identity), departmentWhere],
      },
      select: COLLECTION_PROJECT_SELECT,
      orderBy: [{ name: "asc" }],
    });
    if (records.length === 0) {
      throw new NotFoundException("部门不存在或不在当前数据范围内");
    }
    const projects = records.map((record) =>
      this.mapCollectionProject(record),
    );
    projects.sort((left, right) => {
      const leftRemaining = Number(left.remaining?.toString() ?? -1);
      const rightRemaining = Number(right.remaining?.toString() ?? -1);
      if (leftRemaining !== rightRemaining) {
        return rightRemaining - leftRemaining;
      }
      return left.record.name.localeCompare(right.record.name, "zh-CN");
    });
    return {
      departmentId: records[0]?.department?.id ?? null,
      departmentKey,
      departmentName: records[0]?.department?.name ?? "未分配部门",
      summary: this.collectionTotals(projects),
      projects: projects.map(({ item }) => item),
      updatedAt: this.latestCollectionTimestamp(projects),
    };
  }

  async riskCollections(
    identity: SessionIdentity,
    query: ListRiskCollectionsQueryDto,
  ): Promise<RiskCollectionListResponse> {
    const filters: Prisma.ProjectWhereInput[] = [
      this.projectScope(identity),
      {
        risks: {
          some: {
            status: RiskStatus.ACTIVE,
          },
        },
      },
    ];
    if (query.keyword?.trim()) {
      const keyword = query.keyword.trim();
      filters.push({
        OR: [
          { name: { contains: keyword, mode: "insensitive" } },
          { externalCode: { contains: keyword, mode: "insensitive" } },
          {
            collectionProgress: {
              contains: keyword,
              mode: "insensitive",
            },
          },
        ],
      });
    }
    if (query.owner?.trim()) {
      filters.push({ deliveryOwnerName: query.owner.trim() });
    }
    if (query.level) {
      filters.push({
        risks: {
          some: {
            status: RiskStatus.ACTIVE,
            level: query.level,
          },
        },
      });
    }

    const records = await this.prisma.project.findMany({
      where: { AND: filters },
      select: COLLECTION_PROJECT_SELECT,
      orderBy: [{ name: "asc" }],
    });
    const projects = records
      .map((record) => this.mapRiskCollectionProject(record))
      .sort((left, right) => {
        const levelOrder = { HIGH: 0, MEDIUM: 1, LOW: 2, UNKNOWN: 3 };
        const levelDelta =
          levelOrder[left.item.riskLevel] -
          levelOrder[right.item.riskLevel];
        if (levelDelta !== 0) return levelDelta;
        const remainingDelta =
          Number(right.remaining?.toString() ?? -1) -
          Number(left.remaining?.toString() ?? -1);
        if (remainingDelta !== 0) return remainingDelta;
        return left.record.name.localeCompare(right.record.name, "zh-CN");
      });
    const owners = Array.from(
      new Set(
        records
          .map(({ deliveryOwnerName }) => deliveryOwnerName)
          .filter((value): value is string => Boolean(value)),
      ),
    ).sort((left, right) => left.localeCompare(right, "zh-CN"));

    return {
      items: projects.map(({ item }) => item),
      totals: this.collectionTotals(projects),
      riskProjectTotal: projects.length,
      owners,
      updatedAt: this.latestRiskCollectionTimestamp(projects),
      dataScope: identity.user.dataScope,
    };
  }

  async riskCollectionDetail(
    identity: SessionIdentity,
    projectId: string,
  ): Promise<RiskCollectionDetail> {
    const record = await this.prisma.project.findFirst({
      where: {
        id: projectId,
        AND: [
          this.projectScope(identity),
          {
            risks: {
              some: {
                status: RiskStatus.ACTIVE,
              },
            },
          },
        ],
      },
      select: COLLECTION_PROJECT_SELECT,
    });
    if (!record) {
      throw new NotFoundException("风险项目不存在或不在当前数据范围内");
    }
    const project = this.mapRiskCollectionProject(record);
    return {
      ...project.item,
      monthlyCollections: project.monthlyCollections.map((item) => ({
        month: item.month,
        attribute: item.attribute,
        amountYuan: item.amount?.toFixed(2) ?? null,
      })),
      activeRisks: record.risks.map((risk) => ({
        id: risk.id,
        title: risk.title,
        description: risk.description,
        level: risk.level,
        categoryName: risk.category.name,
        sourceLabel: this.riskSourceLabel(risk.sourceType),
        detectedAt: risk.detectedAt.toISOString(),
      })),
      statisticalScope:
        "仅统计当前存在有效风险的项目；Excel 空金额不按 0 计算，已确认关联的涵谷回款可作为补充金额来源。",
    };
  }

  async riskTimeline(
    identity: SessionIdentity,
    query: ListRiskTimelineQueryDto,
  ): Promise<RiskTimelineListResponse> {
    const scopedWhere: Prisma.RiskTimelineEventWhereInput = {
      project: {
        is: this.projectScope(identity),
      },
    };
    const filters: Prisma.RiskTimelineEventWhereInput[] = [
      scopedWhere,
    ];
    if (query.keyword?.trim()) {
      const keyword = query.keyword.trim();
      filters.push({
        OR: [
          { title: { contains: keyword, mode: "insensitive" } },
          { description: { contains: keyword, mode: "insensitive" } },
          {
            project: {
              is: {
                name: { contains: keyword, mode: "insensitive" },
              },
            },
          },
          {
            risk: {
              is: {
                title: { contains: keyword, mode: "insensitive" },
              },
            },
          },
        ],
      });
    }
    if (query.level) {
      filters.push({
        risk: {
          is: {
            level: query.level,
          },
        },
      });
    }
    if (query.eventType) {
      filters.push({ eventType: query.eventType });
    }
    if (query.projectId) {
      filters.push({ projectId: query.projectId });
    }
    const where: Prisma.RiskTimelineEventWhereInput = {
      AND: filters,
    };
    const [
      records,
      total,
      scopedTotal,
      riskCreated,
      riskChanged,
      actionProgress,
      resolved,
      projects,
      latest,
    ] =
      await this.prisma.$transaction([
        this.prisma.riskTimelineEvent.findMany({
          where,
          include: TIMELINE_INCLUDE,
          orderBy: [{ occurredAt: "desc" }, { createdAt: "desc" }],
          skip: (query.page - 1) * query.pageSize,
          take: query.pageSize,
        }),
        this.prisma.riskTimelineEvent.count({ where }),
        this.prisma.riskTimelineEvent.count({
          where: scopedWhere,
        }),
        this.prisma.riskTimelineEvent.count({
          where: {
            AND: [
              scopedWhere,
              { eventType: RiskTimelineEventType.RISK_CREATED },
            ],
          },
        }),
        this.prisma.riskTimelineEvent.count({
          where: {
            AND: [
              scopedWhere,
              {
                eventType: {
                  in: [
                    RiskTimelineEventType.RISK_UPDATED,
                    RiskTimelineEventType.LEVEL_CHANGED,
                    RiskTimelineEventType.RISK_REOPENED,
                  ],
                },
              },
            ],
          },
        }),
        this.prisma.riskTimelineEvent.count({
          where: {
            AND: [
              scopedWhere,
              {
                eventType: {
                  in: [
                    RiskTimelineEventType.ACTION_CREATED,
                    RiskTimelineEventType.ACTION_UPDATED,
                    RiskTimelineEventType.ACTION_STATUS_CHANGED,
                    RiskTimelineEventType.ACTION_COMPLETED,
                  ],
                },
              },
            ],
          },
        }),
        this.prisma.riskTimelineEvent.count({
          where: {
            AND: [
              scopedWhere,
              { eventType: RiskTimelineEventType.RISK_RESOLVED },
            ],
          },
        }),
        this.prisma.project.findMany({
          where: {
            AND: [
              this.projectScope(identity),
              {
                riskTimelineEvents: {
                  some: {},
                },
              },
            ],
          },
          select: { id: true, name: true },
          orderBy: { name: "asc" },
        }),
        this.prisma.riskTimelineEvent.findFirst({
          where: scopedWhere,
          select: { occurredAt: true },
          orderBy: { occurredAt: "desc" },
        }),
      ]);
    return {
      items: records.map((record) => this.mapTimeline(record)),
      page: query.page,
      pageSize: query.pageSize,
      total,
      summary: {
        total: scopedTotal,
        riskCreated,
        riskChanged,
        actionProgress,
        resolved,
      },
      projects,
      updatedAt: latest?.occurredAt.toISOString() ?? null,
      dataScope: identity.user.dataScope,
    };
  }

  async riskTimelineDetail(
    identity: SessionIdentity,
    id: string,
  ): Promise<RiskTimelineDetail> {
    const record = await this.prisma.riskTimelineEvent.findFirst({
      where: {
        id,
        project: {
          is: this.projectScope(identity),
        },
      },
      include: TIMELINE_INCLUDE,
    });
    if (!record) {
      throw new NotFoundException("时间线事件不存在或不在当前数据范围内");
    }
    const metadata =
      record.metadata &&
      typeof record.metadata === "object" &&
      !Array.isArray(record.metadata)
        ? (record.metadata as Record<string, unknown>)
        : null;
    return {
      ...this.mapTimeline(record),
      riskDescription: record.risk.description,
      riskEvidence: record.risk.evidence,
      riskSuggestion: record.risk.suggestion,
      detectedAt: record.risk.detectedAt.toISOString(),
      resolvedAt: record.risk.resolvedAt?.toISOString() ?? null,
      resolutionReason: record.risk.resolutionReason,
      actionItem: record.actionItem
        ? {
            id: record.actionItem.id,
            title: record.actionItem.title,
            status: record.actionItem.status,
            assigneeName:
              record.actionItem.assigneeUser?.displayName ??
              record.actionItem.assigneeNameSource ??
              "待分配",
            dueDate:
              record.actionItem.dueDate?.toISOString().slice(0, 10) ??
              null,
            completionNote: record.actionItem.completionNote,
          }
        : null,
      metadata,
    };
  }

  async focus(identity: SessionIdentity): Promise<DashboardFocusItem[]> {
    const records = await this.prisma.risk.findMany({
      where: this.activeRiskWhere(this.projectScope(identity)),
      include: RISK_INCLUDE,
      orderBy: [{ level: "asc" }, { updatedAt: "desc" }],
      take: 8,
    });
    return records
      .sort((left, right) => {
        const levelOrder = { HIGH: 0, MEDIUM: 1, LOW: 2, UNKNOWN: 3 };
        const levelDelta =
          levelOrder[left.level] - levelOrder[right.level];
        if (levelDelta !== 0) return levelDelta;
        return (
          Number(right.project.remainingAmount ?? 0) -
          Number(left.project.remainingAmount ?? 0)
        );
      })
      .slice(0, 5)
      .map((record) => this.mapRisk(record));
  }

  async list(
    identity: SessionIdentity,
    query: ListRisksQueryDto,
  ): Promise<DashboardRiskListResponse> {
    const where = this.listWhere(identity, query);
    const [items, total] = await this.prisma.$transaction([
      this.prisma.risk.findMany({
        where,
        include: RISK_INCLUDE,
        orderBy: [{ level: "asc" }, { updatedAt: "desc" }],
        skip: (query.page - 1) * query.pageSize,
        take: query.pageSize,
      }),
      this.prisma.risk.count({ where }),
    ]);
    return {
      items: items.map((item) => this.mapRisk(item)),
      page: query.page,
      pageSize: query.pageSize,
      total,
    };
  }

  async resolvedRisks(
    identity: SessionIdentity,
    query: ListResolvedRisksQueryDto,
  ): Promise<ResolvedRiskListResponse> {
    const where = this.resolvedRiskWhere(identity, query);
    const scope = this.projectScope(identity);
    const scopedResolvedWhere: Prisma.RiskWhereInput = {
      status: RiskStatus.RESOLVED,
      project: { is: scope },
    };
    const [items, total, owners, latest] = await this.prisma.$transaction([
      this.prisma.risk.findMany({
        where,
        include: RISK_INCLUDE,
        orderBy: [{ resolvedAt: "desc" }, { updatedAt: "desc" }],
        skip: (query.page - 1) * query.pageSize,
        take: query.pageSize,
      }),
      this.prisma.risk.count({ where }),
      this.prisma.project.findMany({
        where: {
          AND: [
            scope,
            { deliveryOwnerName: { not: null } },
            {
              risks: {
                some: { status: RiskStatus.RESOLVED },
              },
            },
          ],
        },
        distinct: ["deliveryOwnerName"],
        select: { deliveryOwnerName: true },
        orderBy: { deliveryOwnerName: "asc" },
      }),
      this.prisma.risk.findFirst({
        where: scopedResolvedWhere,
        select: { resolvedAt: true },
        orderBy: { resolvedAt: "desc" },
      }),
    ]);
    return {
      items: items.map((item) => this.mapResolvedRisk(item)),
      page: query.page,
      pageSize: query.pageSize,
      total,
      owners: owners
        .map(({ deliveryOwnerName }) => deliveryOwnerName)
        .filter((value): value is string => Boolean(value)),
      updatedAt: latest?.resolvedAt?.toISOString() ?? null,
      dataScope: identity.user.dataScope,
    };
  }

  async filterOptions(
    identity: SessionIdentity,
  ): Promise<DashboardRiskFilterOptions> {
    const projectScope = this.projectScope(identity);
    const [categories, ownerProjects] = await this.prisma.$transaction([
      this.prisma.riskCategory.findMany({
        where: { isActive: true },
        select: { id: true, code: true, name: true },
        orderBy: [{ sortOrder: "asc" }, { name: "asc" }],
      }),
      this.prisma.project.findMany({
        where: {
          AND: [
            projectScope,
            {
              deliveryOwnerName: { not: null },
            },
          ],
        },
        distinct: ["deliveryOwnerName"],
        select: { deliveryOwnerName: true },
        orderBy: { deliveryOwnerName: "asc" },
      }),
    ]);
    return {
      categories,
      owners: ownerProjects
        .map(({ deliveryOwnerName }) => deliveryOwnerName)
        .filter((value): value is string => Boolean(value)),
    };
  }

  async detail(
    identity: SessionIdentity,
    id: string,
  ): Promise<DashboardRiskDetail> {
    const record = await this.prisma.risk.findFirst({
      where: {
        id,
        project: {
          is: this.projectScope(identity),
        },
      },
      include: RISK_INCLUDE,
    });
    if (!record) {
      throw new NotFoundException("风险不存在或不在当前数据范围内");
    }
    const sameProject = await this.prisma.risk.findMany({
      where: {
        projectId: record.projectId,
        id: { not: record.id },
      },
      select: {
        id: true,
        title: true,
        level: true,
        status: true,
        category: {
          select: {
            name: true,
          },
        },
      },
      orderBy: [{ status: "asc" }, { level: "asc" }, { updatedAt: "desc" }],
      take: 10,
    });
    return {
      ...this.mapRisk(record),
      resolvedAt: record.resolvedAt?.toISOString() ?? null,
      resolvedByName: record.resolvedBy?.displayName ?? null,
      resolutionReason: record.resolutionReason,
      sameProjectRisks: sameProject.map((risk) => ({
        id: risk.id,
        title: risk.title,
        level: risk.level,
        status: risk.status,
        categoryName: risk.category.name,
      })),
    };
  }

  private projectScope(identity: SessionIdentity): Prisma.ProjectWhereInput {
    return this.dataScopes.forUser(
      identity.user.id,
      identity.user.dataScope as DataScopeType,
    );
  }

  private mapCollectionProject(
    record: CollectionProjectRecord,
  ): CollectionProjectView {
    const amounts = resolveProjectCollectionAmounts({
      annualPlanAmount: record.annualPlanAmount,
      actualCollectedAmount: record.actualCollectedAmount,
      remainingAmount: record.remainingAmount,
      supplementalRows: record.supplementalCollectionRows,
    });
    const sourceLabel = {
      PROJECT_LIST: "项目清单 Excel",
      SUPPLEMENTAL: "涵谷回款",
      MISSING: "数据待补充",
    }[amounts.source];
    const updatedAt = [
      record.lastImportedAt,
      ...record.supplementalCollectionRows.map((row) => row.updatedAt),
    ]
      .filter((value): value is Date => Boolean(value))
      .sort((left, right) => right.getTime() - left.getTime())[0] ?? null;
    return {
      record,
      receivable: amounts.receivable,
      collected: amounts.collected,
      remaining: amounts.remaining,
      complete: amounts.complete,
      updatedAt,
      item: {
        projectId: record.id,
        externalCode: record.externalCode,
        projectName: record.name,
        ownerName: record.deliveryOwnerName,
        amountSource: amounts.source,
        amountSourceLabel: sourceLabel,
        supplementalRowCount: amounts.supplementalRowCount,
        receivableAmountYuan:
          amounts.receivable?.toFixed(2) ?? null,
        collectedAmountYuan: amounts.collected?.toFixed(2) ?? null,
        remainingAmountYuan: amounts.remaining?.toFixed(2) ?? null,
        completionRate: collectionCompletionRate(
          amounts.receivable,
          amounts.collected,
        ),
      },
    };
  }

  private mapRiskCollectionProject(
    record: CollectionProjectRecord,
  ): RiskCollectionProjectView {
    const base = this.mapCollectionProject(record);
    const monthlyCollections =
      base.item.amountSource === "SUPPLEMENTAL"
        ? this.mergeMonthlyCollections(
            record.supplementalCollectionRows.flatMap((row) =>
              this.parseMonthlyCollections(row.monthlyCollections),
            ),
          )
        : this.parseMonthlyCollections(record.monthlyCollections);
    const next = resolveNextCollection(
      monthlyCollections,
      record.collectionProgress,
      new Date().getMonth() + 1,
    );
    const riskLevel = this.highestRiskLevel(
      record.risks.map(({ level }) => level),
    );
    const latestRiskUpdate = record.risks
      .map(({ updatedAt }) => updatedAt)
      .sort((left, right) => right.getTime() - left.getTime())[0];
    const updatedAt = [base.updatedAt, latestRiskUpdate]
      .filter((value): value is Date => Boolean(value))
      .sort((left, right) => right.getTime() - left.getTime())[0] ?? null;
    return {
      ...base,
      updatedAt,
      monthlyCollections,
      item: {
        ...base.item,
        departmentName: record.department?.name ?? null,
        riskLevel,
        activeRiskTotal: record.risks.length,
        collectionProgress: record.collectionProgress,
        nextCollection: {
          source: next.source,
          month: next.month,
          attribute: next.attribute,
          amountYuan: next.amount?.toFixed(2) ?? null,
          label: next.label,
        },
        updatedAt: updatedAt?.toISOString() ?? null,
      },
    };
  }

  private parseMonthlyCollections(
    value: Prisma.JsonValue | null,
  ): MonthlyCollectionAmount[] {
    if (!Array.isArray(value)) return [];
    return value
      .map((entry): MonthlyCollectionAmount | null => {
        if (
          typeof entry !== "object" ||
          entry === null ||
          Array.isArray(entry)
        ) {
          return null;
        }
        const month = Number(entry.month);
        if (!Number.isInteger(month) || month < 1 || month > 12) {
          return null;
        }
        let amount: Prisma.Decimal | null = null;
        if (
          typeof entry.amount === "string" ||
          typeof entry.amount === "number"
        ) {
          try {
            amount = new Prisma.Decimal(entry.amount);
          } catch {
            amount = null;
          }
        }
        return {
          month,
          amount,
          attribute:
            typeof entry.attribute === "string"
              ? entry.attribute
              : null,
        };
      })
      .filter((entry): entry is MonthlyCollectionAmount => Boolean(entry))
      .sort((left, right) => left.month - right.month);
  }

  private mergeMonthlyCollections(
    entries: MonthlyCollectionAmount[],
  ): MonthlyCollectionAmount[] {
    const months = new Map<number, MonthlyCollectionAmount>();
    for (const entry of entries) {
      const current = months.get(entry.month);
      months.set(entry.month, {
        month: entry.month,
        amount:
          current?.amount && entry.amount
            ? current.amount.add(entry.amount)
            : current?.amount ?? entry.amount,
        attribute: current?.attribute ?? entry.attribute,
      });
    }
    return Array.from(months.values()).sort(
      (left, right) => left.month - right.month,
    );
  }

  private highestRiskLevel(
    levels: ProjectRiskLevel[],
  ): ProjectRiskLevel {
    const order: Record<ProjectRiskLevel, number> = {
      HIGH: 3,
      MEDIUM: 2,
      LOW: 1,
      UNKNOWN: 0,
    };
    return levels.reduce(
      (highest, level) =>
        order[level] > order[highest] ? level : highest,
      ProjectRiskLevel.UNKNOWN,
    );
  }

  private collectionTotals(
    projects: CollectionProjectView[],
  ): DepartmentCollectionTotals {
    const complete = projects.filter((project) => project.complete);
    const sum = (
      field: "receivable" | "collected" | "remaining",
    ): Prisma.Decimal | null =>
      complete.length > 0
        ? complete.reduce(
            (total, project) => total.add(project[field]!),
            new Prisma.Decimal(0),
          )
        : null;
    const receivable = sum("receivable");
    const collected = sum("collected");
    const remaining = sum("remaining");
    return {
      projectTotal: projects.length,
      amountCompleteProjectTotal: complete.length,
      amountMissingProjectTotal: projects.length - complete.length,
      receivableAmountYuan: receivable?.toFixed(2) ?? null,
      collectedAmountYuan: collected?.toFixed(2) ?? null,
      remainingAmountYuan: remaining?.toFixed(2) ?? null,
      completionRate: collectionCompletionRate(receivable, collected),
    };
  }

  private latestCollectionTimestamp(
    projects: CollectionProjectView[],
  ): string | null {
    const latest = projects
      .map(({ updatedAt }) => updatedAt)
      .filter((value): value is Date => Boolean(value))
      .sort((left, right) => right.getTime() - left.getTime())[0];
    return latest?.toISOString() ?? null;
  }

  private latestRiskCollectionTimestamp(
    projects: RiskCollectionProjectView[],
  ): string | null {
    const latest = projects
      .map(({ updatedAt }) => updatedAt)
      .filter((value): value is Date => Boolean(value))
      .sort((left, right) => right.getTime() - left.getTime())[0];
    return latest?.toISOString() ?? null;
  }

  private activeRiskWhere(
    projectScope: Prisma.ProjectWhereInput,
  ): Prisma.RiskWhereInput {
    return {
      status: RiskStatus.ACTIVE,
      project: {
        is: projectScope,
      },
    };
  }

  private listWhere(
    identity: SessionIdentity,
    query: ListRisksQueryDto,
  ): Prisma.RiskWhereInput {
    const filters: Prisma.RiskWhereInput[] = [
      this.activeRiskWhere(this.projectScope(identity)),
    ];
    if (query.keyword?.trim()) {
      const keyword = query.keyword.trim();
      filters.push({
        OR: [
          { title: { contains: keyword, mode: "insensitive" } },
          { description: { contains: keyword, mode: "insensitive" } },
          {
            project: {
              is: {
                name: { contains: keyword, mode: "insensitive" },
              },
            },
          },
        ],
      });
    }
    if (query.level) filters.push({ level: query.level });
    if (query.categoryId) filters.push({ categoryId: query.categoryId });
    if (query.sourceType) filters.push({ sourceType: query.sourceType });
    if (query.owner?.trim()) {
      filters.push({
        project: {
          is: {
            deliveryOwnerName: query.owner.trim(),
          },
        },
      });
    }
    return { AND: filters };
  }

  private resolvedRiskWhere(
    identity: SessionIdentity,
    query: ListResolvedRisksQueryDto,
  ): Prisma.RiskWhereInput {
    const filters: Prisma.RiskWhereInput[] = [
      {
        status: RiskStatus.RESOLVED,
        project: {
          is: this.projectScope(identity),
        },
      },
    ];
    if (query.keyword?.trim()) {
      const keyword = query.keyword.trim();
      filters.push({
        OR: [
          { title: { contains: keyword, mode: "insensitive" } },
          { description: { contains: keyword, mode: "insensitive" } },
          {
            resolutionReason: {
              contains: keyword,
              mode: "insensitive",
            },
          },
          {
            project: {
              is: {
                name: { contains: keyword, mode: "insensitive" },
              },
            },
          },
        ],
      });
    }
    if (query.level) filters.push({ level: query.level });
    if (query.categoryId) filters.push({ categoryId: query.categoryId });
    if (query.sourceType) filters.push({ sourceType: query.sourceType });
    if (query.owner?.trim()) {
      filters.push({
        project: {
          is: {
            deliveryOwnerName: query.owner.trim(),
          },
        },
      });
    }
    return { AND: filters };
  }

  private mapRisk(record: RiskRecord): DashboardRiskListItem {
    return {
      id: record.id,
      projectId: record.project.id,
      projectExternalCode: record.project.externalCode,
      projectName: record.project.name,
      departmentName: record.project.department?.name ?? null,
      projectOwnerName: record.project.deliveryOwnerName,
      title: record.title,
      description: record.description,
      evidence: record.evidence,
      suggestion: record.suggestion,
      level: record.level,
      status: record.status,
      category: record.category,
      sourceType: record.sourceType,
      sourceLabel: this.riskSourceLabel(record.sourceType),
      reporterName:
        record.reporterUser?.displayName ??
        record.reporterNameSource ??
        null,
      weekCode: record.weekCode,
      actualCollectedAmountYuan:
        record.project.actualCollectedAmount?.toFixed(2) ?? null,
      remainingAmountYuan:
        record.project.remainingAmount?.toFixed(2) ?? null,
      detectedAt: record.detectedAt.toISOString(),
      updatedAt: record.updatedAt.toISOString(),
    };
  }

  private mapResolvedRisk(record: RiskRecord): ResolvedRiskListItem {
    return {
      ...this.mapRisk(record),
      resolvedAt: record.resolvedAt!.toISOString(),
      resolvedByName: record.resolvedBy?.displayName ?? "系统处理",
      resolutionReason: record.resolutionReason ?? "未记录解除原因",
    };
  }

  private mapTimeline(record: TimelineRecord): RiskTimelineItem {
    const presentation = eventPresentation(record.eventType);
    return {
      id: record.id,
      eventType: record.eventType,
      eventLabel: presentation.label,
      tone: presentation.tone,
      projectId: record.project.id,
      projectName: record.project.name,
      departmentName: record.project.department?.name ?? null,
      projectOwnerName: record.project.deliveryOwnerName,
      riskId: record.risk.id,
      riskTitle: record.risk.title,
      riskLevel: record.risk.level,
      riskStatus: record.risk.status,
      categoryName: record.risk.category.name,
      title: record.title,
      description: record.description,
      fromValue: record.fromValue,
      toValue: record.toValue,
      actorName:
        record.actor?.displayName ??
        record.actorNameSource ??
        "系统",
      sourceLabel: this.riskSourceLabel(record.risk.sourceType),
      occurredAt: record.occurredAt.toISOString(),
    };
  }

  private riskSourceLabel(
    sourceType: "EXCEL" | "LITIGATION" | "MAIL_AI" | "MANUAL",
  ): string {
    return {
      EXCEL: "项目清单 Excel",
      LITIGATION: "发函诉讼清单",
      MAIL_AI: "周报邮件 AI 提炼",
      MANUAL: "日常上报",
    }[sourceType];
  }
}
