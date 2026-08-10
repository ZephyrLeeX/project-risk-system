import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  ActionItemStatus,
  DataScopeType,
  Prisma,
  UserStatus,
} from "@prisma/client";
import { randomUUID } from "node:crypto";

import type {
  ManagerTodoDetail,
  ManagerTodoItem,
  ManagerTodoListResponse,
} from "@risk-platform/contracts";

import { AuditService } from "../audit/audit.service";
import type { SessionIdentity } from "../auth/auth.types";
import { PrismaService } from "../prisma/prisma.service";
import { DataScopeService } from "../rbac/data-scope.service";
import {
  buildActionTimelineChange,
  type ActionTimelineSnapshot,
} from "../risk-timeline/risk-timeline-policy";
import { RiskTimelineService } from "../risk-timeline/risk-timeline.service";
import type { ListTodosQueryDto, UpdateTodoDto } from "./dto/todo.dto";
import { buildScheduleSuggestions } from "./todo-policy";

const TODO_INCLUDE = {
  assigneeUser: {
    select: {
      id: true,
      displayName: true,
    },
  },
  project: {
    select: {
      id: true,
      name: true,
      deliveryOwnerName: true,
      department: {
        select: { name: true },
      },
    },
  },
  risk: {
    include: {
      category: {
        select: { name: true },
      },
    },
  },
} satisfies Prisma.ActionItemInclude;

type TodoRecord = Prisma.ActionItemGetPayload<{
  include: typeof TODO_INCLUDE;
}>;

export interface TodoMutationContext {
  identity: SessionIdentity;
  clientIp?: string;
  userAgent?: string;
}

@Injectable()
export class TodosService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly dataScopes: DataScopeService,
    private readonly audit: AuditService,
    private readonly timeline: RiskTimelineService,
  ) {}

  async list(
    identity: SessionIdentity,
    query: ListTodosQueryDto,
  ): Promise<ManagerTodoListResponse> {
    const scope = this.projectScope(identity);
    const scopedWhere: Prisma.ActionItemWhereInput = {
      project: { is: scope },
    };
    const filteredWhere: Prisma.ActionItemWhereInput = {
      AND: [
        scopedWhere,
        ...(query.owner?.trim()
          ? [
              {
                OR: [
                  { assigneeNameSource: query.owner.trim() },
                  {
                    assigneeUser: {
                      is: { displayName: query.owner.trim() },
                    },
                  },
                ],
              } satisfies Prisma.ActionItemWhereInput,
            ]
          : []),
        ...(query.status ? [{ status: query.status }] : []),
      ],
    };
    const [filteredRecords, scopedRecords] = await Promise.all([
      this.prisma.actionItem.findMany({
        where: filteredWhere,
        include: TODO_INCLUDE,
        orderBy: [
          { status: "asc" },
          { urgency: "asc" },
          { dueDate: "asc" },
          { updatedAt: "desc" },
        ],
      }),
      this.prisma.actionItem.findMany({
        where: scopedWhere,
        include: TODO_INCLUDE,
        orderBy: { updatedAt: "desc" },
      }),
    ]);
    const items = filteredRecords.map((record) => this.mapItem(record));
    const allItems = scopedRecords.map((record) => this.mapItem(record));
    const owners = Array.from(
      new Set(allItems.map((item) => item.assigneeName)),
    ).sort((left, right) => left.localeCompare(right, "zh-CN"));
    const latest = scopedRecords[0]?.updatedAt ?? null;

    return {
      items,
      summary: {
        total: allItems.length,
        pending: allItems.filter(({ status }) => status === "PENDING").length,
        inProgress: allItems.filter(
          ({ status }) => status === "IN_PROGRESS",
        ).length,
        completed: allItems.filter(({ status }) => status === "COMPLETED")
          .length,
        emergency: allItems.filter(
          ({ urgency, status }) =>
            urgency === "EMERGENCY" && status !== "COMPLETED",
        ).length,
      },
      owners,
      schedule: buildScheduleSuggestions(items),
      updatedAt: latest?.toISOString() ?? null,
      dataScope: identity.user.dataScope,
    };
  }

  async detail(
    identity: SessionIdentity,
    id: string,
  ): Promise<ManagerTodoDetail> {
    const record = await this.findScoped(identity, id);
    return this.mapDetail(record);
  }

  async update(
    id: string,
    dto: UpdateTodoDto,
    context: TodoMutationContext,
  ): Promise<ManagerTodoDetail> {
    if (
      dto.status === undefined &&
      dto.assigneeName === undefined &&
      dto.dueDate === undefined &&
      dto.completionNote === undefined
    ) {
      throw new BadRequestException("请至少修改一项待办信息");
    }
    if (dto.assigneeName !== undefined && !dto.assigneeName) {
      throw new BadRequestException("负责人不能为空");
    }
    const existing = await this.findScoped(context.identity, id);
    const assignee =
      dto.assigneeName === undefined
        ? undefined
        : await this.resolveAssignee(dto.assigneeName);
    const nextStatus = dto.status ?? existing.status;
    const completed = nextStatus === ActionItemStatus.COMPLETED;
    const data: Prisma.ActionItemUpdateInput = {
      ...(dto.status !== undefined ? { status: dto.status } : {}),
      ...(assignee
        ? {
            assigneeNameSource: assignee.name,
            assigneeUser: assignee.userId
              ? { connect: { id: assignee.userId } }
              : { disconnect: true },
          }
        : {}),
      ...(dto.dueDate !== undefined
        ? {
            dueDate: dto.dueDate
              ? new Date(`${dto.dueDate}T00:00:00.000Z`)
              : null,
          }
        : {}),
      ...(dto.completionNote !== undefined
        ? { completionNote: dto.completionNote?.trim() || null }
        : {}),
      ...(dto.status !== undefined
        ? completed
          ? {
              completedAt: new Date(),
              completedBy: { connect: { id: context.identity.user.id } },
            }
          : {
              completedAt: null,
              completedBy: { disconnect: true },
            }
        : {}),
    };
    const updated = await this.prisma.$transaction(async (transaction) => {
      const result = await transaction.actionItem.update({
        where: { id },
        data,
        include: TODO_INCLUDE,
      });
      if (result.riskId) {
        const change = buildActionTimelineChange(
          this.timelineSnapshot(existing),
          this.timelineSnapshot(result),
        );
        await this.timeline.record(transaction, {
          projectId: result.projectId,
          riskId: result.riskId,
          actionItemId: result.id,
          eventType: change.eventType,
          title: change.title,
          description: change.description,
          fromValue: change.fromValue,
          toValue: change.toValue,
          actorUserId: context.identity.user.id,
          actorNameSource: context.identity.user.displayName,
          occurredAt: result.updatedAt,
          metadata: {
            status: result.status,
            assigneeName:
              result.assigneeUser?.displayName ??
              result.assigneeNameSource,
            dueDate:
              result.dueDate?.toISOString().slice(0, 10) ?? null,
          },
        });
      }
      return result;
    });

    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "TODO",
      action: "ACTION_ITEM_UPDATED",
      resourceType: "ACTION_ITEM",
      resourceId: id,
      result: "SUCCESS",
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      beforeSnapshot: this.auditSnapshot(existing),
      afterSnapshot: this.auditSnapshot(updated),
    });
    return this.mapDetail(updated);
  }

  private async findScoped(
    identity: SessionIdentity,
    id: string,
  ): Promise<TodoRecord> {
    const record = await this.prisma.actionItem.findFirst({
      where: {
        id,
        project: { is: this.projectScope(identity) },
      },
      include: TODO_INCLUDE,
    });
    if (!record) throw new NotFoundException("待办事项不存在或已超出数据范围");
    return record;
  }

  private async resolveAssignee(
    name: string,
  ): Promise<{ name: string; userId: string | null }> {
    const normalized = name.trim();
    const users = await this.prisma.user.findMany({
      where: {
        displayName: normalized,
        status: UserStatus.ACTIVE,
      },
      select: { id: true },
      take: 2,
    });
    return {
      name: normalized,
      userId: users.length === 1 ? users[0]!.id : null,
    };
  }

  private projectScope(identity: SessionIdentity): Prisma.ProjectWhereInput {
    return this.dataScopes.forUser(
      identity.user.id,
      identity.user.dataScope as DataScopeType,
    );
  }

  private mapItem(record: TodoRecord): ManagerTodoItem {
    return {
      id: record.id,
      riskId: record.riskId,
      projectId: record.projectId,
      projectName: record.project.name,
      projectOwnerName: record.project.deliveryOwnerName,
      departmentName: record.project.department?.name ?? null,
      title: record.title,
      description: record.description,
      urgency: record.urgency,
      status: record.status,
      sourceType: record.sourceType,
      typeLabel: record.risk?.category.name ?? "一般处理事项",
      assigneeUserId: record.assigneeUserId,
      assigneeName:
        record.assigneeUser?.displayName ??
        record.assigneeNameSource ??
        "待分配",
      dueDate: record.dueDate?.toISOString().slice(0, 10) ?? null,
      completionNote: record.completionNote,
      completedAt: record.completedAt?.toISOString() ?? null,
      createdAt: record.createdAt.toISOString(),
      updatedAt: record.updatedAt.toISOString(),
    };
  }

  private mapDetail(record: TodoRecord): ManagerTodoDetail {
    return {
      ...this.mapItem(record),
      risk: record.risk
        ? {
            id: record.risk.id,
            title: record.risk.title,
            description: record.risk.description,
            evidence: record.risk.evidence,
            suggestion: record.risk.suggestion,
            level: record.risk.level,
            status: record.risk.status,
            categoryName: record.risk.category.name,
            sourceLabel: this.sourceLabel(record.risk.sourceType),
            detectedAt: record.risk.detectedAt.toISOString(),
          }
        : null,
    };
  }

  private sourceLabel(source: string): string {
    return (
      {
        EXCEL: "项目清单 Excel",
        LITIGATION: "发函诉讼清单",
        MAIL_AI: "周报邮件 AI 提炼",
        MANUAL: "日常上报",
      }[source] ?? "其他来源"
    );
  }

  private auditSnapshot(record: TodoRecord): Prisma.InputJsonValue {
    return {
      status: record.status,
      assigneeName:
        record.assigneeUser?.displayName ?? record.assigneeNameSource,
      dueDate: record.dueDate?.toISOString().slice(0, 10) ?? null,
      completionNote: record.completionNote,
      completedAt: record.completedAt?.toISOString() ?? null,
    };
  }

  private timelineSnapshot(
    record: TodoRecord,
  ): ActionTimelineSnapshot {
    return {
      status: record.status,
      assigneeName:
        record.assigneeUser?.displayName ??
        record.assigneeNameSource,
      dueDate: record.dueDate?.toISOString().slice(0, 10) ?? null,
      completionNote: record.completionNote,
    };
  }
}
