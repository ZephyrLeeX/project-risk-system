-- CreateEnum
CREATE TYPE "SupplementalMatchStatus" AS ENUM ('MATCHED', 'UNMATCHED', 'AMBIGUOUS');

-- AlterTable
ALTER TABLE "import_batches"
ADD COLUMN "supplementalTotalRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "supplementalMatchedRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "supplementalUnmatchedRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "supplementalAmbiguousRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "supplementalWarningRows" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "supplementalErrorRows" INTEGER NOT NULL DEFAULT 0;

-- CreateTable
CREATE TABLE "supplemental_collection_rows" (
  "id" UUID NOT NULL,
  "batchId" UUID NOT NULL,
  "rowNumber" INTEGER NOT NULL,
  "sourceSheet" VARCHAR(128) NOT NULL DEFAULT '涵谷回款',
  "sourceKey" VARCHAR(64) NOT NULL,
  "status" "ImportRowStatus" NOT NULL,
  "matchStatus" "SupplementalMatchStatus" NOT NULL,
  "matchedImportKey" VARCHAR(64),
  "projectId" UUID,
  "externalCode" VARCHAR(128),
  "projectName" VARCHAR(500),
  "contractReceivableAmount" DECIMAL(18,2),
  "procurementContractAmount" DECIMAL(18,2),
  "cumulativeCollectedAmount" DECIMAL(18,2),
  "remainingUncollectedAmount" DECIMAL(18,2),
  "actualCollectedThisYear" DECIMAL(18,2),
  "actualCollectedNetThisYear" DECIMAL(18,2),
  "annualCollectionPlan" DECIMAL(18,2),
  "collectionRiskLevel" "ProjectRiskLevel" NOT NULL DEFAULT 'UNKNOWN',
  "monthlyCollections" JSONB,
  "monthAttributes" JSONB,
  "afterYearAmount" DECIMAL(18,2),
  "sourceSnapshot" JSONB NOT NULL,
  "warnings" JSONB,
  "errors" JSONB,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,

  CONSTRAINT "supplemental_collection_rows_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "supplemental_collection_rows_batchId_rowNumber_key"
ON "supplemental_collection_rows"("batchId", "rowNumber");

-- CreateIndex
CREATE INDEX "supplemental_collection_rows_batchId_status_idx"
ON "supplemental_collection_rows"("batchId", "status");

-- CreateIndex
CREATE INDEX "supplemental_collection_rows_matchStatus_idx"
ON "supplemental_collection_rows"("matchStatus");

-- CreateIndex
CREATE INDEX "supplemental_collection_rows_projectId_idx"
ON "supplemental_collection_rows"("projectId");

-- CreateIndex
CREATE INDEX "supplemental_collection_rows_sourceKey_idx"
ON "supplemental_collection_rows"("sourceKey");

-- AddForeignKey
ALTER TABLE "supplemental_collection_rows"
ADD CONSTRAINT "supplemental_collection_rows_batchId_fkey"
FOREIGN KEY ("batchId") REFERENCES "import_batches"("id")
ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "supplemental_collection_rows"
ADD CONSTRAINT "supplemental_collection_rows_projectId_fkey"
FOREIGN KEY ("projectId") REFERENCES "projects"("id")
ON DELETE SET NULL ON UPDATE CASCADE;
