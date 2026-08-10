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
