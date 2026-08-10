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
