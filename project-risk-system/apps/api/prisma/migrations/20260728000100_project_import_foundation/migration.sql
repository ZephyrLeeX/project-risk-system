-- CreateEnum
CREATE TYPE "ProjectRiskLevel" AS ENUM ('HIGH', 'MEDIUM', 'LOW', 'UNKNOWN');

-- CreateEnum
CREATE TYPE "ImportBatchStatus" AS ENUM ('PREVIEWED', 'IMPORTED', 'ROLLED_BACK', 'FAILED');

-- CreateEnum
CREATE TYPE "ImportRowStatus" AS ENUM ('READY', 'WARNING', 'ERROR', 'IMPORTED', 'ROLLED_BACK');

-- CreateEnum
CREATE TYPE "ImportRowAction" AS ENUM ('CREATE', 'UPDATE', 'SKIP');

-- AlterTable
ALTER TABLE "projects"
  ADD COLUMN "importKey" VARCHAR(64),
  ADD COLUMN "deliveryOwnerName" VARCHAR(128),
  ADD COLUMN "annualPlanAmount" DECIMAL(18,2),
  ADD COLUMN "actualCollectedAmount" DECIMAL(18,2),
  ADD COLUMN "remainingAmount" DECIMAL(18,2),
  ADD COLUMN "monthlyCollections" JSONB,
  ADD COLUMN "monthAttributes" JSONB,
  ADD COLUMN "collectionRiskLevel" "ProjectRiskLevel" NOT NULL DEFAULT 'UNKNOWN',
  ADD COLUMN "collectionProgress" TEXT,
  ADD COLUMN "lastImportedAt" TIMESTAMPTZ(3);

-- CreateTable
CREATE TABLE "import_batches" (
    "id" UUID NOT NULL,
    "fileName" VARCHAR(255) NOT NULL,
    "fileHash" VARCHAR(64) NOT NULL,
    "storageKey" VARCHAR(500) NOT NULL,
    "status" "ImportBatchStatus" NOT NULL DEFAULT 'PREVIEWED',
    "sheetName" VARCHAR(128) NOT NULL,
    "sourceMeta" JSONB,
    "totalRows" INTEGER NOT NULL,
    "readyRows" INTEGER NOT NULL,
    "warningRows" INTEGER NOT NULL,
    "errorRows" INTEGER NOT NULL,
    "createdRows" INTEGER NOT NULL DEFAULT 0,
    "updatedRows" INTEGER NOT NULL DEFAULT 0,
    "uploadedById" UUID NOT NULL,
    "confirmedById" UUID,
    "rolledBackById" UUID,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "confirmedAt" TIMESTAMPTZ(3),
    "rolledBackAt" TIMESTAMPTZ(3),

    CONSTRAINT "import_batches_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "project_import_rows" (
    "id" UUID NOT NULL,
    "batchId" UUID NOT NULL,
    "rowNumber" INTEGER NOT NULL,
    "importKey" VARCHAR(64) NOT NULL,
    "action" "ImportRowAction" NOT NULL,
    "status" "ImportRowStatus" NOT NULL,
    "externalCode" VARCHAR(128),
    "projectName" VARCHAR(255),
    "departmentName" VARCHAR(128),
    "deliveryOwnerName" VARCHAR(128),
    "annualPlanAmount" DECIMAL(18,2),
    "actualCollectedAmount" DECIMAL(18,2),
    "remainingAmount" DECIMAL(18,2),
    "monthlyCollections" JSONB,
    "monthAttributes" JSONB,
    "collectionRiskLevel" "ProjectRiskLevel" NOT NULL DEFAULT 'UNKNOWN',
    "collectionProgress" TEXT,
    "sourceSnapshot" JSONB NOT NULL,
    "warnings" JSONB,
    "errors" JSONB,
    "matchedProjectId" UUID,
    "committedProjectId" UUID,
    "beforeSnapshot" JSONB,
    "afterSnapshot" JSONB,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "project_import_rows_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "projects_importKey_key" ON "projects"("importKey");

-- CreateIndex
CREATE INDEX "import_batches_fileHash_idx" ON "import_batches"("fileHash");

-- CreateIndex
CREATE INDEX "import_batches_status_createdAt_idx" ON "import_batches"("status", "createdAt");

-- CreateIndex
CREATE INDEX "import_batches_uploadedById_idx" ON "import_batches"("uploadedById");

-- CreateIndex
CREATE UNIQUE INDEX "project_import_rows_batchId_rowNumber_key" ON "project_import_rows"("batchId", "rowNumber");

-- CreateIndex
CREATE INDEX "project_import_rows_batchId_status_idx" ON "project_import_rows"("batchId", "status");

-- CreateIndex
CREATE INDEX "project_import_rows_importKey_idx" ON "project_import_rows"("importKey");

-- CreateIndex
CREATE INDEX "project_import_rows_matchedProjectId_idx" ON "project_import_rows"("matchedProjectId");

-- CreateIndex
CREATE INDEX "project_import_rows_committedProjectId_idx" ON "project_import_rows"("committedProjectId");

-- AddForeignKey
ALTER TABLE "import_batches" ADD CONSTRAINT "import_batches_uploadedById_fkey" FOREIGN KEY ("uploadedById") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "import_batches" ADD CONSTRAINT "import_batches_confirmedById_fkey" FOREIGN KEY ("confirmedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "import_batches" ADD CONSTRAINT "import_batches_rolledBackById_fkey" FOREIGN KEY ("rolledBackById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "project_import_rows" ADD CONSTRAINT "project_import_rows_batchId_fkey" FOREIGN KEY ("batchId") REFERENCES "import_batches"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "project_import_rows" ADD CONSTRAINT "project_import_rows_matchedProjectId_fkey" FOREIGN KEY ("matchedProjectId") REFERENCES "projects"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "project_import_rows" ADD CONSTRAINT "project_import_rows_committedProjectId_fkey" FOREIGN KEY ("committedProjectId") REFERENCES "projects"("id") ON DELETE SET NULL ON UPDATE CASCADE;
