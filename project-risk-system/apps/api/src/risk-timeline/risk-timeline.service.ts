import { Injectable } from "@nestjs/common";
import {
  type Prisma,
  type RiskTimelineEventType,
} from "@prisma/client";

type TimelineClient = Pick<
  Prisma.TransactionClient,
  "riskTimelineEvent"
>;

export interface RecordRiskTimelineEvent {
  projectId: string;
  riskId: string;
  actionItemId?: string | null;
  eventType: RiskTimelineEventType;
  title: string;
  description: string;
  fromValue?: string | null;
  toValue?: string | null;
  actorUserId?: string | null;
  actorNameSource?: string | null;
  sourceBatchId?: string | null;
  occurredAt?: Date;
  metadata?: Prisma.InputJsonValue;
}

@Injectable()
export class RiskTimelineService {
  async record(
    client: TimelineClient,
    event: RecordRiskTimelineEvent,
  ): Promise<void> {
    await client.riskTimelineEvent.create({
      data: {
        projectId: event.projectId,
        riskId: event.riskId,
        actionItemId: event.actionItemId,
        eventType: event.eventType,
        title: event.title,
        description: event.description,
        fromValue: event.fromValue,
        toValue: event.toValue,
        actorUserId: event.actorUserId,
        actorNameSource: event.actorNameSource,
        sourceBatchId: event.sourceBatchId,
        occurredAt: event.occurredAt,
        metadata: event.metadata,
      },
    });
  }
}
