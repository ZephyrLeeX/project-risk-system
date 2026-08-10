-- CreateEnum
CREATE TYPE "RiskTimelineEventType" AS ENUM (
  'RISK_CREATED',
  'RISK_UPDATED',
  'LEVEL_CHANGED',
  'ACTION_CREATED',
  'ACTION_UPDATED',
  'ACTION_STATUS_CHANGED',
  'ACTION_COMPLETED',
  'RISK_RESOLVED',
  'RISK_REOPENED'
);

-- CreateTable
CREATE TABLE "risk_timeline_events" (
  "id" UUID NOT NULL,
  "projectId" UUID NOT NULL,
  "riskId" UUID NOT NULL,
  "actionItemId" UUID,
  "eventType" "RiskTimelineEventType" NOT NULL,
  "title" VARCHAR(250) NOT NULL,
  "description" TEXT NOT NULL,
  "fromValue" VARCHAR(128),
  "toValue" VARCHAR(128),
  "actorUserId" UUID,
  "actorNameSource" VARCHAR(128),
  "sourceBatchId" UUID,
  "occurredAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "metadata" JSONB,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "risk_timeline_events_pkey" PRIMARY KEY ("id")
);

-- Backfill existing risks so upgraded environments receive a complete baseline.
INSERT INTO "risk_timeline_events" (
  "id",
  "projectId",
  "riskId",
  "eventType",
  "title",
  "description",
  "actorUserId",
  "actorNameSource",
  "sourceBatchId",
  "occurredAt",
  "metadata"
)
SELECT
  md5(r."id"::text || ':risk-created')::uuid,
  r."projectId",
  r."id",
  'RISK_CREATED'::"RiskTimelineEventType",
  '风险首次识别',
  r."description",
  r."reporterUserId",
  r."reporterNameSource",
  r."sourceBatchId",
  r."detectedAt",
  jsonb_build_object('level', r."level", 'sourceType', r."sourceType")
FROM "risks" r;

-- CreateIndex
CREATE INDEX "risk_timeline_events_projectId_occurredAt_idx"
ON "risk_timeline_events"("projectId", "occurredAt");

CREATE INDEX "risk_timeline_events_riskId_occurredAt_idx"
ON "risk_timeline_events"("riskId", "occurredAt");

CREATE INDEX "risk_timeline_events_actionItemId_idx"
ON "risk_timeline_events"("actionItemId");

CREATE INDEX "risk_timeline_events_eventType_occurredAt_idx"
ON "risk_timeline_events"("eventType", "occurredAt");

CREATE INDEX "risk_timeline_events_sourceBatchId_idx"
ON "risk_timeline_events"("sourceBatchId");

-- AddForeignKey
ALTER TABLE "risk_timeline_events"
ADD CONSTRAINT "risk_timeline_events_projectId_fkey"
FOREIGN KEY ("projectId") REFERENCES "projects"("id")
ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "risk_timeline_events"
ADD CONSTRAINT "risk_timeline_events_riskId_fkey"
FOREIGN KEY ("riskId") REFERENCES "risks"("id")
ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "risk_timeline_events"
ADD CONSTRAINT "risk_timeline_events_actionItemId_fkey"
FOREIGN KEY ("actionItemId") REFERENCES "action_items"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "risk_timeline_events"
ADD CONSTRAINT "risk_timeline_events_actorUserId_fkey"
FOREIGN KEY ("actorUserId") REFERENCES "users"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "risk_timeline_events"
ADD CONSTRAINT "risk_timeline_events_sourceBatchId_fkey"
FOREIGN KEY ("sourceBatchId") REFERENCES "import_batches"("id")
ON DELETE CASCADE ON UPDATE CASCADE;
