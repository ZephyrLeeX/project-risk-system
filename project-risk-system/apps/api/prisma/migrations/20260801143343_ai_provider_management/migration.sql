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
