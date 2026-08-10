-- CreateEnum
CREATE TYPE "MailMessageStatus" AS ENUM ('ANALYZING', 'COMPLETED', 'SKIPPED', 'FAILED');
CREATE TYPE "MailMessageSkipReason" AS ENUM ('DUPLICATE', 'RULE_MISMATCH');
CREATE TYPE "MailProjectMatchType" AS ENUM ('EXACT', 'ALIAS', 'FUZZY', 'MANUAL');
CREATE TYPE "MailRiskCandidateStatus" AS ENUM ('PENDING', 'CONFIRMED', 'IGNORED');

-- AlterEnum
ALTER TYPE "MailSyncTrigger" ADD VALUE 'RETRY';

-- AlterTable (backfill protects installations that already have queued batches)
ALTER TABLE "mail_sync_batches" ADD COLUMN "code" VARCHAR(64), ADD COLUMN "retryOfId" UUID, ADD COLUMN "targetMessageId" UUID;
UPDATE "mail_sync_batches"
SET "code" = CONCAT('SYNC-', TO_CHAR("createdAt" AT TIME ZONE 'Asia/Shanghai', 'YYYYMMDD-HH24MI'), '-', UPPER(SUBSTRING(REPLACE("id"::text, '-', ''), 1, 6)))
WHERE "code" IS NULL;
ALTER TABLE "mail_sync_batches" ALTER COLUMN "code" SET NOT NULL;

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
