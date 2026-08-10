-- CreateEnum
CREATE TYPE "ActionItemUrgency" AS ENUM ('EMERGENCY', 'HIGH', 'NORMAL');

-- CreateEnum
CREATE TYPE "ActionItemStatus" AS ENUM ('PENDING', 'IN_PROGRESS', 'COMPLETED');

-- CreateEnum
CREATE TYPE "ActionItemSourceType" AS ENUM ('RISK_SUGGESTION', 'MANUAL');

-- CreateTable
CREATE TABLE "action_items" (
  "id" UUID NOT NULL,
  "riskId" UUID,
  "projectId" UUID NOT NULL,
  "title" VARCHAR(250) NOT NULL,
  "description" TEXT NOT NULL,
  "urgency" "ActionItemUrgency" NOT NULL,
  "status" "ActionItemStatus" NOT NULL DEFAULT 'PENDING',
  "sourceType" "ActionItemSourceType" NOT NULL DEFAULT 'RISK_SUGGESTION',
  "assigneeUserId" UUID,
  "assigneeNameSource" VARCHAR(128),
  "dueDate" DATE,
  "completionNote" TEXT,
  "createdById" UUID,
  "completedById" UUID,
  "completedAt" TIMESTAMPTZ(3),
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,

  CONSTRAINT "action_items_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "action_items_riskId_key" ON "action_items"("riskId");

-- CreateIndex
CREATE INDEX "action_items_projectId_status_idx" ON "action_items"("projectId", "status");

-- CreateIndex
CREATE INDEX "action_items_assigneeUserId_status_idx" ON "action_items"("assigneeUserId", "status");

-- CreateIndex
CREATE INDEX "action_items_assigneeNameSource_status_idx" ON "action_items"("assigneeNameSource", "status");

-- CreateIndex
CREATE INDEX "action_items_urgency_status_idx" ON "action_items"("urgency", "status");

-- CreateIndex
CREATE INDEX "action_items_dueDate_idx" ON "action_items"("dueDate");

-- AddForeignKey
ALTER TABLE "action_items"
ADD CONSTRAINT "action_items_riskId_fkey"
FOREIGN KEY ("riskId") REFERENCES "risks"("id")
ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "action_items"
ADD CONSTRAINT "action_items_projectId_fkey"
FOREIGN KEY ("projectId") REFERENCES "projects"("id")
ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "action_items"
ADD CONSTRAINT "action_items_assigneeUserId_fkey"
FOREIGN KEY ("assigneeUserId") REFERENCES "users"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "action_items"
ADD CONSTRAINT "action_items_createdById_fkey"
FOREIGN KEY ("createdById") REFERENCES "users"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "action_items"
ADD CONSTRAINT "action_items_completedById_fkey"
FOREIGN KEY ("completedById") REFERENCES "users"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

-- Backfill existing active risks so the new module is immediately usable.
INSERT INTO "action_items" (
  "id",
  "riskId",
  "projectId",
  "title",
  "description",
  "urgency",
  "status",
  "sourceType",
  "assigneeNameSource",
  "createdAt",
  "updatedAt"
)
SELECT
  gen_random_uuid(),
  risk."id",
  risk."projectId",
  LEFT(risk."title" || '处理事项', 250),
  COALESCE(NULLIF(BTRIM(risk."suggestion"), ''), risk."description"),
  CASE risk."level"
    WHEN 'HIGH' THEN 'EMERGENCY'::"ActionItemUrgency"
    WHEN 'MEDIUM' THEN 'HIGH'::"ActionItemUrgency"
    ELSE 'NORMAL'::"ActionItemUrgency"
  END,
  'PENDING'::"ActionItemStatus",
  'RISK_SUGGESTION'::"ActionItemSourceType",
  CASE
    WHEN risk."level" = 'HIGH' THEN '管理者'
    ELSE COALESCE(NULLIF(BTRIM(project."deliveryOwnerName"), ''), '管理者')
  END,
  CURRENT_TIMESTAMP,
  CURRENT_TIMESTAMP
FROM "risks" AS risk
JOIN "projects" AS project ON project."id" = risk."projectId"
WHERE risk."status" = 'ACTIVE'
ON CONFLICT ("riskId") DO NOTHING;
