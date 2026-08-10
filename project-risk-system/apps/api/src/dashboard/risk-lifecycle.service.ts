import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  ActionItemStatus,
  AuditResult,
  DataScopeType,
  Prisma,
  RiskStatus,
} from "@prisma/client";
import { randomUUID } from "node:crypto";

import type { SessionIdentity } from "../auth/auth.types";
import { PrismaService } from "../prisma/prisma.service";
import { DataScopeService } from "../rbac/data-scope.service";
import { RiskTimelineService } from "../risk-timeline/risk-timeline.service";
import {
  defaultAssigneeForRisk,
  urgencyForRisk,
} from "../todos/todo-policy";

const LIFECYCLE_RISK_INCLUDE = {
  project: {
    select: {
      deliveryOwnerName: true,
    },
  },
  actionItem: true,
} satisfies Prisma.RiskInclude;

type LifecycleRisk = Prisma.RiskGetPayload<{
  include: typeof LIFECYCLE_RISK_INCLUDE;
}>;

export interface RiskLifecycleContext {
  identity: SessionIdentity;
  clientIp?: string;
  userAgent?: string;
}

@Injectable()
export class RiskLifecycleService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly dataScopes: DataScopeService,
    private readonly timeline: RiskTimelineService,
  ) {}

  async resolve(
    id: string,
    reason: string,
    context: RiskLifecycleContext,
  ): Promise<void> {
    const existing = await this.findScoped(context.identity, id);
    if (existing.status === RiskStatus.RESOLVED) {
      throw new BadRequestException("该风险已经解除，无需重复操作");
    }

    const occurredAt = new Date();
    await this.prisma.$transaction(async (transaction) => {
      await transaction.risk.update({
        where: { id },
        data: {
          status: RiskStatus.RESOLVED,
          resolvedAt: occurredAt,
          resolvedBy: {
            connect: { id: context.identity.user.id },
          },
          resolutionReason: reason,
        },
      });

      if (
        existing.actionItem &&
        existing.actionItem.status !== ActionItemStatus.COMPLETED
      ) {
        await transaction.actionItem.update({
          where: { id: existing.actionItem.id },
          data: {
            status: ActionItemStatus.COMPLETED,
            completionNote: this.resolutionNote(
              existing.actionItem.completionNote,
              reason,
            ),
            completedAt: occurredAt,
            completedBy: {
              connect: { id: context.identity.user.id },
            },
          },
        });
        await this.timeline.record(transaction, {
          projectId: existing.projectId,
          riskId: existing.id,
          actionItemId: existing.actionItem.id,
          eventType: "ACTION_COMPLETED",
          title: "待办事项随风险解除完成",
          description: `风险已解除，关联待办同步完成：${reason}`,
          fromValue: existing.actionItem.status,
          toValue: ActionItemStatus.COMPLETED,
          actorUserId: context.identity.user.id,
          actorNameSource: context.identity.user.displayName,
          occurredAt,
          metadata: {
            resolutionReason: reason,
          },
        });
      }

      await this.timeline.record(transaction, {
        projectId: existing.projectId,
        riskId: existing.id,
        actionItemId: existing.actionItem?.id ?? null,
        eventType: "RISK_RESOLVED",
        title: "风险已解除",
        description: reason,
        fromValue: RiskStatus.ACTIVE,
        toValue: RiskStatus.RESOLVED,
        actorUserId: context.identity.user.id,
        actorNameSource: context.identity.user.displayName,
        occurredAt,
        metadata: {
          resolutionReason: reason,
        },
      });

      await transaction.auditLog.create({
        data: {
          actorUserId: context.identity.user.id,
          module: "RISK",
          action: "RISK_RESOLVED",
          resourceType: "RISK",
          resourceId: id,
          result: AuditResult.SUCCESS,
          traceId: randomUUID(),
          clientIp: context.clientIp?.slice(0, 64),
          userAgent: context.userAgent?.slice(0, 500),
          beforeSnapshot: this.auditSnapshot(existing),
          afterSnapshot: {
            status: RiskStatus.RESOLVED,
            resolvedAt: occurredAt.toISOString(),
            resolvedById: context.identity.user.id,
            resolutionReason: reason,
          },
        },
      });
    });
  }

  async reopen(
    id: string,
    reason: string,
    context: RiskLifecycleContext,
  ): Promise<void> {
    const existing = await this.findScoped(context.identity, id);
    if (existing.status === RiskStatus.ACTIVE) {
      throw new BadRequestException("该风险当前处于有效状态，无需重新打开");
    }

    const occurredAt = new Date();
    await this.prisma.$transaction(async (transaction) => {
      await transaction.risk.update({
        where: { id },
        data: {
          status: RiskStatus.ACTIVE,
          resolvedAt: null,
          resolvedBy: { disconnect: true },
          resolutionReason: null,
        },
      });

      let actionItemId: string;
      if (existing.actionItem) {
        await transaction.actionItem.update({
          where: { id: existing.actionItem.id },
          data: {
            status: ActionItemStatus.PENDING,
            completionNote: null,
            completedAt: null,
            completedBy: { disconnect: true },
          },
        });
        actionItemId = existing.actionItem.id;
        if (existing.actionItem.status !== ActionItemStatus.PENDING) {
          await this.timeline.record(transaction, {
            projectId: existing.projectId,
            riskId: existing.id,
            actionItemId,
            eventType: "ACTION_STATUS_CHANGED",
            title: "风险重启后待办恢复处理",
            description: `风险重新打开，关联待办已恢复为待处理：${reason}`,
            fromValue: existing.actionItem.status,
            toValue: ActionItemStatus.PENDING,
            actorUserId: context.identity.user.id,
            actorNameSource: context.identity.user.displayName,
            occurredAt,
            metadata: {
              reopenReason: reason,
            },
          });
        }
      } else {
        const action = await transaction.actionItem.create({
          data: {
            riskId: existing.id,
            projectId: existing.projectId,
            title: `${existing.title}处理事项`.slice(0, 250),
            description:
              existing.suggestion?.trim() || existing.description,
            urgency: urgencyForRisk(existing.level),
            status: ActionItemStatus.PENDING,
            sourceType: "RISK_SUGGESTION",
            assigneeNameSource: defaultAssigneeForRisk(
              existing.level,
              existing.project.deliveryOwnerName,
            ),
            createdById: context.identity.user.id,
          },
        });
        actionItemId = action.id;
        await this.timeline.record(transaction, {
          projectId: existing.projectId,
          riskId: existing.id,
          actionItemId,
          eventType: "ACTION_CREATED",
          title: "风险重启后生成处理待办",
          description: action.description,
          actorUserId: context.identity.user.id,
          actorNameSource: context.identity.user.displayName,
          occurredAt,
          metadata: {
            urgency: action.urgency,
            assigneeName: action.assigneeNameSource,
          },
        });
      }

      await this.timeline.record(transaction, {
        projectId: existing.projectId,
        riskId: existing.id,
        actionItemId,
        eventType: "RISK_REOPENED",
        title: "风险重新进入跟踪",
        description: reason,
        fromValue: RiskStatus.RESOLVED,
        toValue: RiskStatus.ACTIVE,
        actorUserId: context.identity.user.id,
        actorNameSource: context.identity.user.displayName,
        occurredAt,
        metadata: {
          reopenReason: reason,
          previousResolutionReason: existing.resolutionReason,
        },
      });

      await transaction.auditLog.create({
        data: {
          actorUserId: context.identity.user.id,
          module: "RISK",
          action: "RISK_REOPENED",
          resourceType: "RISK",
          resourceId: id,
          result: AuditResult.SUCCESS,
          traceId: randomUUID(),
          clientIp: context.clientIp?.slice(0, 64),
          userAgent: context.userAgent?.slice(0, 500),
          beforeSnapshot: this.auditSnapshot(existing),
          afterSnapshot: {
            status: RiskStatus.ACTIVE,
            reopenedAt: occurredAt.toISOString(),
            reopenReason: reason,
          },
        },
      });
    });
  }

  private async findScoped(
    identity: SessionIdentity,
    id: string,
  ): Promise<LifecycleRisk> {
    const record = await this.prisma.risk.findFirst({
      where: {
        id,
        project: {
          is: this.dataScopes.forUser(
            identity.user.id,
            identity.user.dataScope as DataScopeType,
          ),
        },
      },
      include: LIFECYCLE_RISK_INCLUDE,
    });
    if (!record) {
      throw new NotFoundException("风险不存在或不在当前数据范围内");
    }
    return record;
  }

  private auditSnapshot(record: LifecycleRisk): Prisma.InputJsonValue {
    return {
      status: record.status,
      resolvedAt: record.resolvedAt?.toISOString() ?? null,
      resolvedById: record.resolvedById,
      resolutionReason: record.resolutionReason,
      actionItemStatus: record.actionItem?.status ?? null,
    };
  }

  private resolutionNote(
    existing: string | null,
    reason: string,
  ): string {
    return [existing?.trim(), `风险解除：${reason}`]
      .filter((value): value is string => Boolean(value))
      .join("\n")
      .slice(0, 2000);
  }
}
