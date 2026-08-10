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

-- Seed the three fixed risk levels used by the approved prototype.
INSERT INTO "risk_level_rules"
  ("id", "level", "displayName", "colorToken", "criteria", "keywords", "sortOrder", "isActive", "updatedAt")
VALUES
  (gen_random_uuid(), 'HIGH', '高风险', '#EF5555', '重大回款逾期、诉讼或关键交付受阻，需要管理层立即决策。', '["重大逾期","诉讼","关键受阻"]', 10, true, CURRENT_TIMESTAMP),
  (gen_random_uuid(), 'MEDIUM', '中风险', '#F0A019', '存在明确影响，需持续跟踪并制定措施。', '["延期","投诉","审计"]', 20, true, CURRENT_TIMESTAMP),
  (gen_random_uuid(), 'LOW', '低风险', '#21A66D', '影响可控，按计划观察和推进。', '["关注","观察","提示"]', 30, true, CURRENT_TIMESTAMP);
