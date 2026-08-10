-- CreateEnum
CREATE TYPE "LegalMatterMatchStatus" AS ENUM ('MATCHED', 'UNMATCHED', 'AMBIGUOUS');

-- AlterTable
ALTER TABLE "import_batches"
ADD COLUMN "legalTotalRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "legalMatchedRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "legalUnmatchedRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "legalAmbiguousRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "legalWarningRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "legalErrorRows" INTEGER NOT NULL DEFAULT 0;

-- CreateTable
CREATE TABLE "legal_matter_rows" (
  "id" UUID NOT NULL,
  "batchId" UUID NOT NULL,
  "rowNumber" INTEGER NOT NULL,
  "sourceSheet" VARCHAR(128) NOT NULL DEFAULT '发函-诉讼清单',
  "sourceKey" VARCHAR(64) NOT NULL,
  "status" "ImportRowStatus" NOT NULL,
  "matchStatus" "LegalMatterMatchStatus" NOT NULL,
  "matchedImportKey" VARCHAR(64),
  "projectId" UUID,
  "externalCode" VARCHAR(128),
  "projectName" VARCHAR(500),
  "departmentName" VARCHAR(128),
  "deliveryOwnerName" VARCHAR(128),
  "annualPlanAmount" DECIMAL(18,2),
  "collectionRiskLevel" "ProjectRiskLevel" NOT NULL DEFAULT 'UNKNOWN',
  "legalProgress" TEXT,
  "monthlyCollections" JSONB,
  "monthAttributes" JSONB,
  "sourceSnapshot" JSONB NOT NULL,
  "warnings" JSONB,
  "errors" JSONB,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,

  CONSTRAINT "legal_matter_rows_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "legal_matter_rows_batchId_rowNumber_key"
ON "legal_matter_rows"("batchId", "rowNumber");

-- CreateIndex
CREATE INDEX "legal_matter_rows_batchId_status_idx"
ON "legal_matter_rows"("batchId", "status");

-- CreateIndex
CREATE INDEX "legal_matter_rows_matchStatus_idx"
ON "legal_matter_rows"("matchStatus");

-- CreateIndex
CREATE INDEX "legal_matter_rows_projectId_idx"
ON "legal_matter_rows"("projectId");

-- CreateIndex
CREATE INDEX "legal_matter_rows_sourceKey_idx"
ON "legal_matter_rows"("sourceKey");

-- AddForeignKey
ALTER TABLE "legal_matter_rows"
ADD CONSTRAINT "legal_matter_rows_batchId_fkey"
FOREIGN KEY ("batchId") REFERENCES "import_batches"("id")
ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "legal_matter_rows"
ADD CONSTRAINT "legal_matter_rows_projectId_fkey"
FOREIGN KEY ("projectId") REFERENCES "projects"("id")
ON DELETE SET NULL ON UPDATE CASCADE;
