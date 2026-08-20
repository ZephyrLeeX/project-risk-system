-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "public";

-- CreateEnum
CREATE TYPE "UserStatus" AS ENUM ('ACTIVE', 'DISABLED', 'LOCKED');

-- CreateEnum
CREATE TYPE "DataScopeType" AS ENUM ('ALL', 'OWNED', 'ASSIGNED', 'OWNED_OR_ASSIGNED', 'NONE');

-- CreateEnum
CREATE TYPE "ProjectStatus" AS ENUM ('DELIVERY', 'COMPLETED', 'ARCHIVED');

-- CreateEnum
CREATE TYPE "AuditResult" AS ENUM ('SUCCESS', 'FAILURE');

-- CreateTable
CREATE TABLE "departments" (
    "id" UUID NOT NULL,
    "code" VARCHAR(64) NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "parentId" UUID,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "sortOrder" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "departments_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "users" (
    "id" UUID NOT NULL,
    "username" VARCHAR(64) NOT NULL,
    "passwordHash" VARCHAR(255) NOT NULL,
    "displayName" VARCHAR(128) NOT NULL,
    "email" VARCHAR(255),
    "status" "UserStatus" NOT NULL DEFAULT 'ACTIVE',
    "mustChangePassword" BOOLEAN NOT NULL DEFAULT true,
    "failedLoginCount" INTEGER NOT NULL DEFAULT 0,
    "lockedUntil" TIMESTAMPTZ(3),
    "departmentId" UUID,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "passwordChangedAt" TIMESTAMPTZ(3),
    "lastLoginAt" TIMESTAMPTZ(3),

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "roles" (
    "id" UUID NOT NULL,
    "code" VARCHAR(64) NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "description" VARCHAR(500),
    "isSystem" BOOLEAN NOT NULL DEFAULT false,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "defaultDataScope" "DataScopeType" NOT NULL DEFAULT 'ASSIGNED',
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "roles_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "permissions" (
    "id" UUID NOT NULL,
    "code" VARCHAR(128) NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "module" VARCHAR(64) NOT NULL,
    "description" VARCHAR(500),
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "permissions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "user_roles" (
    "userId" UUID NOT NULL,
    "roleId" UUID NOT NULL,
    "dataScope" "DataScopeType" NOT NULL,
    "assignedAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "user_roles_pkey" PRIMARY KEY ("userId","roleId")
);

-- CreateTable
CREATE TABLE "role_permissions" (
    "roleId" UUID NOT NULL,
    "permissionId" UUID NOT NULL,
    "grantedAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "role_permissions_pkey" PRIMARY KEY ("roleId","permissionId")
);

-- CreateTable
CREATE TABLE "projects" (
    "id" UUID NOT NULL,
    "externalCode" VARCHAR(128),
    "name" VARCHAR(255) NOT NULL,
    "alias" VARCHAR(255),
    "status" "ProjectStatus" NOT NULL DEFAULT 'DELIVERY',
    "departmentId" UUID,
    "managerId" UUID,
    "sourceVersion" INTEGER NOT NULL DEFAULT 1,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "projects_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "project_assignments" (
    "projectId" UUID NOT NULL,
    "userId" UUID NOT NULL,
    "assignedBy" UUID,
    "assignedAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "project_assignments_pkey" PRIMARY KEY ("projectId","userId")
);

-- CreateTable
CREATE TABLE "sessions" (
    "id" UUID NOT NULL,
    "tokenHash" VARCHAR(255) NOT NULL,
    "userId" UUID NOT NULL,
    "expiresAt" TIMESTAMPTZ(3) NOT NULL,
    "revokedAt" TIMESTAMPTZ(3),
    "clientIpHash" VARCHAR(128),
    "userAgent" VARCHAR(500),
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "sessions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "audit_logs" (
    "id" UUID NOT NULL,
    "actorUserId" UUID,
    "module" VARCHAR(64) NOT NULL,
    "action" VARCHAR(128) NOT NULL,
    "resourceType" VARCHAR(128) NOT NULL,
    "resourceId" VARCHAR(128),
    "result" "AuditResult" NOT NULL,
    "traceId" VARCHAR(64) NOT NULL,
    "clientIp" VARCHAR(64),
    "userAgent" VARCHAR(500),
    "beforeSnapshot" JSONB,
    "afterSnapshot" JSONB,
    "errorCode" VARCHAR(128),
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "departments_code_key" ON "departments"("code");

-- CreateIndex
CREATE INDEX "departments_parentId_idx" ON "departments"("parentId");

-- CreateIndex
CREATE UNIQUE INDEX "users_username_key" ON "users"("username");

-- Enforce case-insensitive account uniqueness in addition to application normalization.
CREATE UNIQUE INDEX "users_username_lower_key" ON "users"(LOWER("username"));

-- CreateIndex
CREATE INDEX "users_departmentId_idx" ON "users"("departmentId");

-- CreateIndex
CREATE INDEX "users_status_idx" ON "users"("status");

-- CreateIndex
CREATE UNIQUE INDEX "roles_code_key" ON "roles"("code");

-- CreateIndex
CREATE INDEX "roles_enabled_idx" ON "roles"("enabled");

-- CreateIndex
CREATE UNIQUE INDEX "permissions_code_key" ON "permissions"("code");

-- CreateIndex
CREATE INDEX "permissions_module_idx" ON "permissions"("module");

-- CreateIndex
CREATE INDEX "user_roles_roleId_idx" ON "user_roles"("roleId");

-- CreateIndex
CREATE INDEX "role_permissions_permissionId_idx" ON "role_permissions"("permissionId");

-- CreateIndex
CREATE INDEX "projects_name_idx" ON "projects"("name");

-- CreateIndex
CREATE INDEX "projects_departmentId_idx" ON "projects"("departmentId");

-- CreateIndex
CREATE INDEX "projects_managerId_idx" ON "projects"("managerId");

-- CreateIndex
CREATE INDEX "projects_status_idx" ON "projects"("status");

-- CreateIndex
CREATE UNIQUE INDEX "projects_externalCode_key" ON "projects"("externalCode");

-- CreateIndex
CREATE INDEX "project_assignments_userId_idx" ON "project_assignments"("userId");

-- CreateIndex
CREATE INDEX "project_assignments_assignedBy_idx" ON "project_assignments"("assignedBy");

-- CreateIndex
CREATE UNIQUE INDEX "sessions_tokenHash_key" ON "sessions"("tokenHash");

-- CreateIndex
CREATE INDEX "sessions_userId_idx" ON "sessions"("userId");

-- CreateIndex
CREATE INDEX "sessions_expiresAt_idx" ON "sessions"("expiresAt");

-- CreateIndex
CREATE INDEX "audit_logs_actorUserId_idx" ON "audit_logs"("actorUserId");

-- CreateIndex
CREATE INDEX "audit_logs_module_action_idx" ON "audit_logs"("module", "action");

-- CreateIndex
CREATE INDEX "audit_logs_resourceType_resourceId_idx" ON "audit_logs"("resourceType", "resourceId");

-- CreateIndex
CREATE INDEX "audit_logs_traceId_idx" ON "audit_logs"("traceId");

-- CreateIndex
CREATE INDEX "audit_logs_createdAt_idx" ON "audit_logs"("createdAt");

-- AddForeignKey
ALTER TABLE "departments" ADD CONSTRAINT "departments_parentId_fkey" FOREIGN KEY ("parentId") REFERENCES "departments"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "users" ADD CONSTRAINT "users_departmentId_fkey" FOREIGN KEY ("departmentId") REFERENCES "departments"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "user_roles" ADD CONSTRAINT "user_roles_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "user_roles" ADD CONSTRAINT "user_roles_roleId_fkey" FOREIGN KEY ("roleId") REFERENCES "roles"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "role_permissions" ADD CONSTRAINT "role_permissions_roleId_fkey" FOREIGN KEY ("roleId") REFERENCES "roles"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "role_permissions" ADD CONSTRAINT "role_permissions_permissionId_fkey" FOREIGN KEY ("permissionId") REFERENCES "permissions"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "projects" ADD CONSTRAINT "projects_departmentId_fkey" FOREIGN KEY ("departmentId") REFERENCES "departments"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "projects" ADD CONSTRAINT "projects_managerId_fkey" FOREIGN KEY ("managerId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "project_assignments" ADD CONSTRAINT "project_assignments_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "project_assignments" ADD CONSTRAINT "project_assignments_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "project_assignments" ADD CONSTRAINT "project_assignments_assignedBy_fkey" FOREIGN KEY ("assignedBy") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_actorUserId_fkey" FOREIGN KEY ("actorUserId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
-- CreateEnum
CREATE TYPE "ProjectScopeSource" AS ENUM ('ADMIN', 'IMPORT');

-- RenameTable
ALTER TABLE "project_assignments" RENAME TO "user_project_scopes";

-- Rename indexes and constraints to keep database names aligned with the model.
ALTER INDEX "project_assignments_pkey" RENAME TO "user_project_scopes_pkey";
ALTER INDEX "project_assignments_userId_idx" RENAME TO "user_project_scopes_userId_idx";
ALTER INDEX "project_assignments_assignedBy_idx" RENAME TO "user_project_scopes_assignedBy_idx";
ALTER TABLE "user_project_scopes"
  RENAME CONSTRAINT "project_assignments_projectId_fkey" TO "user_project_scopes_projectId_fkey";
ALTER TABLE "user_project_scopes"
  RENAME CONSTRAINT "project_assignments_userId_fkey" TO "user_project_scopes_userId_fkey";
ALTER TABLE "user_project_scopes"
  RENAME CONSTRAINT "project_assignments_assignedBy_fkey" TO "user_project_scopes_assignedBy_fkey";

-- AlterTable
ALTER TABLE "user_project_scopes"
  ADD COLUMN "scopeSource" "ProjectScopeSource" NOT NULL DEFAULT 'ADMIN';
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
-- CreateEnum
CREATE TYPE "AiConnectionStatus" AS ENUM ('UNTESTED', 'HEALTHY', 'FAILED');

-- CreateEnum
CREATE TYPE "AiCallResult" AS ENUM ('SUCCESS', 'FAILURE');

-- CreateEnum
CREATE TYPE "AiCallScene" AS ENUM ('WEEKLY_REPORT', 'AGENT_QUERY', 'RISK_EXTRACTION', 'CONNECTION_TEST');

-- CreateTable
CREATE TABLE "ai_provider_configs" (
    "id" UUID NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "vendor" VARCHAR(128) NOT NULL,
    "endpoint" VARCHAR(500) NOT NULL,
    "model" VARCHAR(128) NOT NULL,
    "encryptedApiKey" TEXT NOT NULL,
    "keyIv" VARCHAR(64) NOT NULL,
    "keyAuthTag" VARCHAR(64) NOT NULL,
    "keyLast4" VARCHAR(16) NOT NULL,
    "expiresAt" DATE,
    "timeoutSeconds" INTEGER NOT NULL DEFAULT 60,
    "retryCount" INTEGER NOT NULL DEFAULT 2,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "isDefault" BOOLEAN NOT NULL DEFAULT false,
    "priority" INTEGER NOT NULL DEFAULT 100,
    "lastTestStatus" "AiConnectionStatus" NOT NULL DEFAULT 'UNTESTED',
    "lastTestAt" TIMESTAMPTZ(3),
    "lastTestLatencyMs" INTEGER,
    "lastTestErrorCode" VARCHAR(128),
    "createdById" UUID,
    "updatedById" UUID,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "ai_provider_configs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ai_call_logs" (
    "id" UUID NOT NULL,
    "traceId" VARCHAR(64) NOT NULL,
    "providerId" UUID,
    "providerNameSnapshot" VARCHAR(128) NOT NULL,
    "modelSnapshot" VARCHAR(128) NOT NULL,
    "scene" "AiCallScene" NOT NULL,
    "inputTokens" INTEGER NOT NULL DEFAULT 0,
    "outputTokens" INTEGER NOT NULL DEFAULT 0,
    "totalTokens" INTEGER NOT NULL DEFAULT 0,
    "durationMs" INTEGER NOT NULL,
    "result" "AiCallResult" NOT NULL,
    "errorCode" VARCHAR(128),
    "errorSummary" VARCHAR(500),
    "actorUserId" UUID,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ai_call_logs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "ai_provider_configs_name_key" ON "ai_provider_configs"("name");

-- CreateIndex
CREATE INDEX "ai_provider_configs_enabled_isDefault_idx" ON "ai_provider_configs"("enabled", "isDefault");

-- CreateIndex
CREATE INDEX "ai_provider_configs_lastTestStatus_lastTestAt_idx" ON "ai_provider_configs"("lastTestStatus", "lastTestAt");

-- CreateIndex
CREATE INDEX "ai_provider_configs_expiresAt_idx" ON "ai_provider_configs"("expiresAt");

-- CreateIndex
CREATE UNIQUE INDEX "ai_call_logs_traceId_key" ON "ai_call_logs"("traceId");

-- CreateIndex
CREATE INDEX "ai_call_logs_providerId_createdAt_idx" ON "ai_call_logs"("providerId", "createdAt");

-- CreateIndex
CREATE INDEX "ai_call_logs_scene_createdAt_idx" ON "ai_call_logs"("scene", "createdAt");

-- CreateIndex
CREATE INDEX "ai_call_logs_result_createdAt_idx" ON "ai_call_logs"("result", "createdAt");

-- CreateIndex
CREATE INDEX "ai_call_logs_actorUserId_createdAt_idx" ON "ai_call_logs"("actorUserId", "createdAt");

-- AddForeignKey
ALTER TABLE "ai_provider_configs" ADD CONSTRAINT "ai_provider_configs_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ai_provider_configs" ADD CONSTRAINT "ai_provider_configs_updatedById_fkey" FOREIGN KEY ("updatedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ai_call_logs" ADD CONSTRAINT "ai_call_logs_providerId_fkey" FOREIGN KEY ("providerId") REFERENCES "ai_provider_configs"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ai_call_logs" ADD CONSTRAINT "ai_call_logs_actorUserId_fkey" FOREIGN KEY ("actorUserId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
-- ExtendTable
ALTER TABLE "risk_categories"
  ADD COLUMN "colorToken" VARCHAR(16) NOT NULL DEFAULT '#4C8FE8',
  ADD COLUMN "description" VARCHAR(500),
  ADD COLUMN "defaultLevel" "ProjectRiskLevel";

-- CreateTable
CREATE TABLE "risk_level_rules" (
  "id" UUID NOT NULL,
  "level" "ProjectRiskLevel" NOT NULL,
  "displayName" VARCHAR(32) NOT NULL,
  "colorToken" VARCHAR(16) NOT NULL,
  "criteria" VARCHAR(500) NOT NULL,
  "keywords" JSONB,
  "sortOrder" INTEGER NOT NULL DEFAULT 0,
  "isActive" BOOLEAN NOT NULL DEFAULT true,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,
  CONSTRAINT "risk_level_rules_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "project_aliases" (
  "id" UUID NOT NULL,
  "projectId" UUID NOT NULL,
  "alias" VARCHAR(255) NOT NULL,
  "normalizedAlias" VARCHAR(255) NOT NULL,
  "source" VARCHAR(64) NOT NULL DEFAULT '系统管理员',
  "note" VARCHAR(500),
  "isActive" BOOLEAN NOT NULL DEFAULT true,
  "hitCount" INTEGER NOT NULL DEFAULT 0,
  "lastHitAt" TIMESTAMPTZ(3),
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,
  CONSTRAINT "project_aliases_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "system_config_releases" (
  "id" UUID NOT NULL,
  "version" VARCHAR(32) NOT NULL,
  "module" VARCHAR(32) NOT NULL DEFAULT 'ALL',
  "changeCount" INTEGER NOT NULL,
  "changeSummary" VARCHAR(500) NOT NULL,
  "impactScope" JSONB NOT NULL,
  "beforeSnapshot" JSONB,
  "snapshot" JSONB NOT NULL,
  "publishedById" UUID,
  "traceId" VARCHAR(64) NOT NULL,
  "publishedAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "system_config_releases_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "risk_level_rules_level_key" ON "risk_level_rules"("level");
CREATE INDEX "risk_level_rules_isActive_sortOrder_idx" ON "risk_level_rules"("isActive", "sortOrder");
CREATE UNIQUE INDEX "project_aliases_normalizedAlias_key" ON "project_aliases"("normalizedAlias");
CREATE INDEX "project_aliases_projectId_isActive_idx" ON "project_aliases"("projectId", "isActive");
CREATE UNIQUE INDEX "system_config_releases_version_key" ON "system_config_releases"("version");
CREATE UNIQUE INDEX "system_config_releases_traceId_key" ON "system_config_releases"("traceId");
CREATE INDEX "system_config_releases_publishedAt_idx" ON "system_config_releases"("publishedAt");
CREATE INDEX "system_config_releases_module_publishedAt_idx" ON "system_config_releases"("module", "publishedAt");

-- AddForeignKey
ALTER TABLE "project_aliases" ADD CONSTRAINT "project_aliases_projectId_fkey"
  FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "system_config_releases" ADD CONSTRAINT "system_config_releases_publishedById_fkey"
  FOREIGN KEY ("publishedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;


ALTER TABLE "audit_logs"
  ADD COLUMN "summary" VARCHAR(500),
  ADD COLUMN "isSensitive" BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN "previousHash" VARCHAR(64),
  ADD COLUMN "integrityHash" VARCHAR(64);
CREATE INDEX "audit_logs_isSensitive_createdAt_idx"
  ON "audit_logs"("isSensitive", "createdAt");
-- Personal mailbox configuration and synchronization task foundation.
CREATE TYPE "MailboxProvider" AS ENUM ('QQ', 'IMAP');
CREATE TYPE "MailboxEncryption" AS ENUM ('SSL', 'STARTTLS');
CREATE TYPE "MailboxConnectionStatus" AS ENUM ('UNTESTED', 'HEALTHY', 'FAILED');
CREATE TYPE "MailSyncTrigger" AS ENUM ('MANUAL', 'SCHEDULED');
CREATE TYPE "MailSyncStatus" AS ENUM ('QUEUED', 'RUNNING', 'SUCCESS', 'PARTIAL', 'FAILURE');

CREATE TABLE "mailbox_configs" (
  "id" UUID NOT NULL,
  "userId" UUID NOT NULL,
  "provider" "MailboxProvider" NOT NULL,
  "email" VARCHAR(255) NOT NULL,
  "imapHost" VARCHAR(255) NOT NULL,
  "imapPort" INTEGER NOT NULL,
  "encryption" "MailboxEncryption" NOT NULL,
  "folder" VARCHAR(255) NOT NULL DEFAULT 'INBOX',
  "encryptedAuthCode" TEXT NOT NULL,
  "authCodeIv" VARCHAR(64) NOT NULL,
  "authCodeTag" VARCHAR(64) NOT NULL,
  "authCodeLast4" VARCHAR(16) NOT NULL,
  "subjectKeywords" JSONB NOT NULL,
  "senderRule" VARCHAR(255),
  "initialSyncWeeks" INTEGER NOT NULL DEFAULT 4,
  "readAttachments" BOOLEAN NOT NULL DEFAULT true,
  "aiExtractionEnabled" BOOLEAN NOT NULL DEFAULT true,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "autoSyncEnabled" BOOLEAN NOT NULL DEFAULT true,
  "uidCursor" BIGINT,
  "connectionStatus" "MailboxConnectionStatus" NOT NULL DEFAULT 'UNTESTED',
  "lastTestAt" TIMESTAMPTZ(3),
  "lastTestLatencyMs" INTEGER,
  "lastTestErrorCode" VARCHAR(128),
  "lastTestErrorSummary" VARCHAR(500),
  "lastSyncAt" TIMESTAMPTZ(3),
  "lastSyncStatus" "MailSyncStatus",
  "lastSyncNewCount" INTEGER NOT NULL DEFAULT 0,
  "lastSyncSuccessCount" INTEGER NOT NULL DEFAULT 0,
  "lastSyncRiskCandidateCount" INTEGER NOT NULL DEFAULT 0,
  "lastSyncFailedCount" INTEGER NOT NULL DEFAULT 0,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,
  CONSTRAINT "mailbox_configs_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "mailbox_configs_port_check" CHECK ("imapPort" BETWEEN 1 AND 65535),
  CONSTRAINT "mailbox_configs_weeks_check" CHECK ("initialSyncWeeks" IN (1, 4, 8, 12))
);

CREATE TABLE "mail_sync_batches" (
  "id" UUID NOT NULL,
  "mailboxConfigId" UUID NOT NULL,
  "trigger" "MailSyncTrigger" NOT NULL,
  "status" "MailSyncStatus" NOT NULL DEFAULT 'QUEUED',
  "operatorUserId" UUID,
  "startedAt" TIMESTAMPTZ(3),
  "finishedAt" TIMESTAMPTZ(3),
  "durationMs" INTEGER,
  "scannedCount" INTEGER NOT NULL DEFAULT 0,
  "newCount" INTEGER NOT NULL DEFAULT 0,
  "successCount" INTEGER NOT NULL DEFAULT 0,
  "skippedCount" INTEGER NOT NULL DEFAULT 0,
  "failedCount" INTEGER NOT NULL DEFAULT 0,
  "riskCandidateCount" INTEGER NOT NULL DEFAULT 0,
  "startUid" BIGINT,
  "endUid" BIGINT,
  "errorSummary" VARCHAR(500),
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,
  CONSTRAINT "mail_sync_batches_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "mailbox_configs_userId_key" ON "mailbox_configs"("userId");
CREATE INDEX "mailbox_configs_enabled_autoSyncEnabled_idx" ON "mailbox_configs"("enabled", "autoSyncEnabled");
CREATE INDEX "mailbox_configs_connectionStatus_idx" ON "mailbox_configs"("connectionStatus");
CREATE INDEX "mail_sync_batches_mailboxConfigId_createdAt_idx" ON "mail_sync_batches"("mailboxConfigId", "createdAt");
CREATE INDEX "mail_sync_batches_status_createdAt_idx" ON "mail_sync_batches"("status", "createdAt");
CREATE INDEX "mail_sync_batches_operatorUserId_idx" ON "mail_sync_batches"("operatorUserId");

ALTER TABLE "mailbox_configs" ADD CONSTRAINT "mailbox_configs_userId_fkey"
  FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_sync_batches" ADD CONSTRAINT "mail_sync_batches_mailboxConfigId_fkey"
  FOREIGN KEY ("mailboxConfigId") REFERENCES "mailbox_configs"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_sync_batches" ADD CONSTRAINT "mail_sync_batches_operatorUserId_fkey"
  FOREIGN KEY ("operatorUserId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
-- CreateEnum
CREATE TYPE "MailMessageStatus" AS ENUM ('ANALYZING', 'COMPLETED', 'SKIPPED', 'FAILED');
CREATE TYPE "MailMessageSkipReason" AS ENUM ('DUPLICATE', 'RULE_MISMATCH');
CREATE TYPE "MailProjectMatchType" AS ENUM ('EXACT', 'ALIAS', 'FUZZY', 'MANUAL');
CREATE TYPE "MailRiskCandidateStatus" AS ENUM ('PENDING', 'CONFIRMED', 'IGNORED');

-- AlterEnum
ALTER TYPE "MailSyncTrigger" ADD VALUE 'RETRY';

-- AlterTable. This empty-database baseline needs no historical data backfill.
ALTER TABLE "mail_sync_batches"
  ADD COLUMN "code" VARCHAR(64) NOT NULL,
  ADD COLUMN "retryOfId" UUID,
  ADD COLUMN "targetMessageId" UUID;

-- CreateTable
CREATE TABLE "mail_messages" (
  "id" UUID NOT NULL,
  "mailboxConfigId" UUID NOT NULL,
  "batchId" UUID NOT NULL,
  "messageId" VARCHAR(500) NOT NULL,
  "imapUid" BIGINT NOT NULL,
  "subject" VARCHAR(500) NOT NULL,
  "senderName" VARCHAR(255),
  "senderAddress" VARCHAR(255),
  "sentAt" TIMESTAMPTZ(3),
  "processedAt" TIMESTAMPTZ(3),
  "status" "MailMessageStatus" NOT NULL DEFAULT 'ANALYZING',
  "skipReason" "MailMessageSkipReason",
  "failureCode" VARCHAR(128),
  "failureSummary" VARCHAR(500),
  "sanitizedSummary" TEXT,
  "keyPoints" JSONB,
  "attachmentMetadata" JSONB,
  "processingTrace" JSONB,
  "retryCount" INTEGER NOT NULL DEFAULT 0,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,
  CONSTRAINT "mail_messages_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "mail_message_project_matches" (
  "id" UUID NOT NULL,
  "messageId" UUID NOT NULL,
  "projectId" UUID NOT NULL,
  "matchType" "MailProjectMatchType" NOT NULL,
  "confidence" INTEGER NOT NULL,
  "matchedText" VARCHAR(500) NOT NULL,
  "confirmedById" UUID,
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "mail_message_project_matches_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "mail_risk_candidates" (
  "id" UUID NOT NULL,
  "messageId" UUID NOT NULL,
  "projectId" UUID NOT NULL,
  "categoryId" UUID NOT NULL,
  "level" "ProjectRiskLevel" NOT NULL,
  "description" TEXT NOT NULL,
  "evidence" TEXT NOT NULL,
  "suggestion" TEXT NOT NULL,
  "confidence" INTEGER NOT NULL,
  "status" "MailRiskCandidateStatus" NOT NULL DEFAULT 'PENDING',
  "confirmedRiskId" UUID,
  "reviewedById" UUID,
  "reviewedAt" TIMESTAMPTZ(3),
  "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMPTZ(3) NOT NULL,
  CONSTRAINT "mail_risk_candidates_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "mail_sync_batches_code_key" ON "mail_sync_batches"("code");
CREATE INDEX "mail_sync_batches_retryOfId_idx" ON "mail_sync_batches"("retryOfId");
CREATE INDEX "mail_sync_batches_targetMessageId_idx" ON "mail_sync_batches"("targetMessageId");
CREATE INDEX "mail_messages_mailboxConfigId_messageId_idx" ON "mail_messages"("mailboxConfigId", "messageId");
CREATE UNIQUE INDEX "mail_messages_mailboxConfigId_imapUid_key" ON "mail_messages"("mailboxConfigId", "imapUid");
CREATE INDEX "mail_messages_batchId_status_idx" ON "mail_messages"("batchId", "status");
CREATE INDEX "mail_messages_mailboxConfigId_sentAt_idx" ON "mail_messages"("mailboxConfigId", "sentAt");
CREATE INDEX "mail_messages_status_updatedAt_idx" ON "mail_messages"("status", "updatedAt");
CREATE UNIQUE INDEX "mail_message_project_matches_messageId_projectId_key" ON "mail_message_project_matches"("messageId", "projectId");
CREATE INDEX "mail_message_project_matches_projectId_createdAt_idx" ON "mail_message_project_matches"("projectId", "createdAt");
CREATE INDEX "mail_message_project_matches_confirmedById_idx" ON "mail_message_project_matches"("confirmedById");
CREATE UNIQUE INDEX "mail_risk_candidates_confirmedRiskId_key" ON "mail_risk_candidates"("confirmedRiskId");
CREATE INDEX "mail_risk_candidates_messageId_status_idx" ON "mail_risk_candidates"("messageId", "status");
CREATE INDEX "mail_risk_candidates_projectId_status_idx" ON "mail_risk_candidates"("projectId", "status");
CREATE INDEX "mail_risk_candidates_categoryId_status_idx" ON "mail_risk_candidates"("categoryId", "status");
CREATE INDEX "mail_risk_candidates_reviewedById_idx" ON "mail_risk_candidates"("reviewedById");

-- AddForeignKey
ALTER TABLE "mail_sync_batches" ADD CONSTRAINT "mail_sync_batches_retryOfId_fkey" FOREIGN KEY ("retryOfId") REFERENCES "mail_sync_batches"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "mail_sync_batches" ADD CONSTRAINT "mail_sync_batches_targetMessageId_fkey" FOREIGN KEY ("targetMessageId") REFERENCES "mail_messages"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "mail_messages" ADD CONSTRAINT "mail_messages_mailboxConfigId_fkey" FOREIGN KEY ("mailboxConfigId") REFERENCES "mailbox_configs"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_messages" ADD CONSTRAINT "mail_messages_batchId_fkey" FOREIGN KEY ("batchId") REFERENCES "mail_sync_batches"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_message_project_matches" ADD CONSTRAINT "mail_message_project_matches_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES "mail_messages"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_message_project_matches" ADD CONSTRAINT "mail_message_project_matches_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_message_project_matches" ADD CONSTRAINT "mail_message_project_matches_confirmedById_fkey" FOREIGN KEY ("confirmedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "mail_risk_candidates" ADD CONSTRAINT "mail_risk_candidates_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES "mail_messages"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_risk_candidates" ADD CONSTRAINT "mail_risk_candidates_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "mail_risk_candidates" ADD CONSTRAINT "mail_risk_candidates_categoryId_fkey" FOREIGN KEY ("categoryId") REFERENCES "risk_categories"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "mail_risk_candidates" ADD CONSTRAINT "mail_risk_candidates_confirmedRiskId_fkey" FOREIGN KEY ("confirmedRiskId") REFERENCES "risks"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "mail_risk_candidates" ADD CONSTRAINT "mail_risk_candidates_reviewedById_fkey" FOREIGN KEY ("reviewedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
