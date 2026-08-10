-- CreateEnum
CREATE TYPE "RiskStatus" AS ENUM ('ACTIVE', 'RESOLVED');

-- CreateEnum
CREATE TYPE "RiskSourceType" AS ENUM ('EXCEL', 'LITIGATION', 'MAIL_AI', 'MANUAL');

-- AlterTable
ALTER TABLE "project_import_rows"
ADD COLUMN "committedRiskId" UUID,
ADD COLUMN "beforeRiskSnapshot" JSONB,
ADD COLUMN "afterRiskSnapshot" JSONB;

-- AlterTable
ALTER TABLE "legal_matter_rows"
ADD COLUMN "committedRiskId" UUID,
ADD COLUMN "beforeRiskSnapshot" JSONB,
ADD COLUMN "afterRiskSnapshot" JSONB;

-- CreateTable
CREATE TABLE "risk_categories" (
  "id" UUID NOT NULL,
  "code" VARCHAR(64) NOT NULL,
  "name" VARCHAR(128) NOT NULL,
  "keywords" JSONB,
  "sortOrder" INTEGER NOT NULL DEFAULT 0,
  "isActive" BOOLEAN NOT NULL DEFAULT true,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,

  CONSTRAINT "risk_categories_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "risks" (
  "id" UUID NOT NULL,
  "projectId" UUID NOT NULL,
  "categoryId" UUID NOT NULL,
  "title" VARCHAR(250) NOT NULL,
  "description" TEXT NOT NULL,
  "evidence" TEXT,
  "level" "ProjectRiskLevel" NOT NULL,
  "status" "RiskStatus" NOT NULL DEFAULT 'ACTIVE',
  "sourceType" "RiskSourceType" NOT NULL,
  "sourceBatchId" UUID,
  "sourceRefId" UUID,
  "reporterUserId" UUID,
  "reporterNameSource" VARCHAR(100),
  "weekCode" VARCHAR(20),
  "suggestion" TEXT,
  "detectedAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "resolvedAt" TIMESTAMPTZ(3),
  "resolvedById" UUID,
  "resolutionReason" TEXT,
  "dedupeFingerprint" VARCHAR(64) NOT NULL,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,

  CONSTRAINT "risks_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "risk_categories_code_key" ON "risk_categories"("code");

-- CreateIndex
CREATE INDEX "risk_categories_isActive_sortOrder_idx"
ON "risk_categories"("isActive", "sortOrder");

-- CreateIndex
CREATE UNIQUE INDEX "risks_dedupeFingerprint_key"
ON "risks"("dedupeFingerprint");

-- CreateIndex
CREATE INDEX "risks_projectId_status_idx" ON "risks"("projectId", "status");

-- CreateIndex
CREATE INDEX "risks_categoryId_status_idx" ON "risks"("categoryId", "status");

-- CreateIndex
CREATE INDEX "risks_level_status_idx" ON "risks"("level", "status");

-- CreateIndex
CREATE INDEX "risks_sourceBatchId_idx" ON "risks"("sourceBatchId");

-- CreateIndex
CREATE INDEX "risks_detectedAt_idx" ON "risks"("detectedAt");

-- AddForeignKey
ALTER TABLE "risks"
ADD CONSTRAINT "risks_projectId_fkey"
FOREIGN KEY ("projectId") REFERENCES "projects"("id")
ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "risks"
ADD CONSTRAINT "risks_categoryId_fkey"
FOREIGN KEY ("categoryId") REFERENCES "risk_categories"("id")
ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "risks"
ADD CONSTRAINT "risks_sourceBatchId_fkey"
FOREIGN KEY ("sourceBatchId") REFERENCES "import_batches"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "risks"
ADD CONSTRAINT "risks_reporterUserId_fkey"
FOREIGN KEY ("reporterUserId") REFERENCES "users"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "risks"
ADD CONSTRAINT "risks_resolvedById_fkey"
FOREIGN KEY ("resolvedById") REFERENCES "users"("id")
ON DELETE SET NULL ON UPDATE CASCADE;

-- SeedData
INSERT INTO "risk_categories"
  ("id", "code", "name", "keywords", "sortOrder", "isActive", "createdAt", "updatedAt")
VALUES
  ('00000000-0000-4000-8000-000000000101', 'COLLECTION', '回款风险', '["回款","应收","质保款","验收款"]'::jsonb, 10, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('00000000-0000-4000-8000-000000000102', 'LITIGATION', '发函诉讼风险', '["发函","诉讼","法务","律师函"]'::jsonb, 20, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('00000000-0000-4000-8000-000000000103', 'SUPPLIER', '供应商风险', '["供应商","采购","核减"]'::jsonb, 30, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('00000000-0000-4000-8000-000000000104', 'CUSTOMER', '客户层面风险', '["客户","甲方","业主"]'::jsonb, 40, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('00000000-0000-4000-8000-000000000105', 'COST', '成本风险', '["成本","预算","超支"]'::jsonb, 50, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('00000000-0000-4000-8000-000000000106', 'ACCEPTANCE_DELAY', '验收延期风险', '["验收","延期","拖期"]'::jsonb, 60, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('00000000-0000-4000-8000-000000000107', 'OUT_OF_SCOPE', '超出合同需求', '["合同外","超范围","新增需求"]'::jsonb, 70, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('00000000-0000-4000-8000-000000000108', 'OTHER', '其他风险', '[]'::jsonb, 999, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT ("code") DO UPDATE SET
  "name" = EXCLUDED."name",
  "keywords" = EXCLUDED."keywords",
  "sortOrder" = EXCLUDED."sortOrder",
  "isActive" = EXCLUDED."isActive",
  "updatedAt" = CURRENT_TIMESTAMP;
