CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

CREATE TYPE "AuditActorType" AS ENUM ('USER', 'SYSTEM', 'WORKER', 'AGENT');

DROP INDEX IF EXISTS "audit_logs_isSensitive_createdAt_idx";
ALTER TABLE "audit_logs"
  DROP CONSTRAINT IF EXISTS "audit_logs_actorUserId_fkey",
  DROP COLUMN "clientIp",
  DROP COLUMN "userAgent",
  DROP COLUMN "beforeSnapshot",
  DROP COLUMN "afterSnapshot",
  DROP COLUMN "summary",
  DROP COLUMN "isSensitive";
ALTER TABLE "audit_logs" RENAME COLUMN "errorCode" TO "failureCode";

ALTER TABLE "audit_logs"
  ADD COLUMN "actorType" "AuditActorType",
  ADD COLUMN "requestId" VARCHAR(64),
  ADD COLUMN "projectId" UUID;

UPDATE "audit_logs"
SET "actorType" = CASE WHEN "actorUserId" IS NULL THEN 'SYSTEM' ELSE 'USER' END::"AuditActorType";

ALTER TABLE "audit_logs"
  ALTER COLUMN "actorType" SET NOT NULL,
  ADD CONSTRAINT "audit_logs_module_code"
    CHECK ("module" ~ '^[A-Z][A-Z0-9_.:-]{0,63}$'),
  ADD CONSTRAINT "audit_logs_action_code"
    CHECK ("action" ~ '^[A-Z][A-Z0-9_.:-]{0,127}$'),
  ADD CONSTRAINT "audit_logs_resource_type_code"
    CHECK ("resourceType" ~ '^[A-Z][A-Z0-9_.:-]{0,127}$'),
  ADD CONSTRAINT "audit_logs_resource_id"
    CHECK ("resourceId" IS NULL OR "resourceId" ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$'),
  ADD CONSTRAINT "audit_logs_failure_code"
    CHECK ("failureCode" IS NULL OR "failureCode" ~ '^[A-Z][A-Z0-9_.:-]{0,127}$'),
  ADD CONSTRAINT "audit_logs_actor_identity"
    CHECK ("actorType" NOT IN ('USER', 'AGENT') OR "actorUserId" IS NOT NULL),
  ADD CONSTRAINT "audit_logs_trace_id"
    CHECK ("traceId" ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'),
  ADD CONSTRAINT "audit_logs_request_id"
    CHECK ("requestId" IS NULL OR "requestId" ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');

CREATE INDEX "audit_logs_requestId_idx" ON "audit_logs"("requestId");
CREATE INDEX "audit_logs_projectId_idx" ON "audit_logs"("projectId");
CREATE UNIQUE INDEX "audit_logs_previousHash_key" ON "audit_logs"("previousHash");
CREATE UNIQUE INDEX "audit_logs_integrityHash_key" ON "audit_logs"("integrityHash");

CREATE OR REPLACE FUNCTION audit_log_compute_hash(
  p_id UUID,
  p_actor_user_id UUID,
  p_actor_type TEXT,
  p_module TEXT,
  p_action TEXT,
  p_resource_type TEXT,
  p_resource_id TEXT,
  p_result TEXT,
  p_trace_id TEXT,
  p_request_id TEXT,
  p_project_id UUID,
  p_failure_code TEXT,
  p_previous_hash TEXT,
  p_created_at TIMESTAMPTZ
) RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
AS $$
  SELECT encode(
      public.digest(
      concat_ws('|',
        p_id::text,
        COALESCE(p_actor_user_id::text, ''),
        p_actor_type,
        p_module,
        p_action,
        p_resource_type,
        COALESCE(p_resource_id, ''),
        p_result,
        p_trace_id,
        COALESCE(p_request_id, ''),
        COALESCE(p_project_id::text, ''),
        COALESCE(p_failure_code, ''),
        COALESCE(p_previous_hash, ''),
        (EXTRACT(EPOCH FROM p_created_at) * 1000)::bigint::text
      ),
      'sha256'
    ),
    'hex'
  );
$$;

CREATE OR REPLACE FUNCTION audit_log_append_hash()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  prior_created_at TIMESTAMPTZ;
BEGIN
  PERFORM pg_advisory_xact_lock(918273645);
  SELECT "integrityHash", "createdAt"
  INTO NEW."previousHash", prior_created_at
  FROM "audit_logs"
  ORDER BY "createdAt" DESC, "id" DESC
  LIMIT 1;

  NEW."createdAt" := date_trunc('milliseconds', clock_timestamp());
  IF prior_created_at IS NOT NULL AND NEW."createdAt" <= prior_created_at THEN
    NEW."createdAt" := prior_created_at + INTERVAL '1 millisecond';
  END IF;

  NEW."integrityHash" := audit_log_compute_hash(
    NEW."id", NEW."actorUserId", NEW."actorType"::text, NEW."module", NEW."action",
    NEW."resourceType", NEW."resourceId", NEW."result"::text, NEW."traceId",
    NEW."requestId", NEW."projectId", NEW."failureCode", NEW."previousHash", NEW."createdAt"
  );
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION audit_log_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'audit_logs are append-only';
END;
$$;

CREATE TRIGGER "audit_logs_append_hash"
BEFORE INSERT ON "audit_logs"
FOR EACH ROW EXECUTE FUNCTION audit_log_append_hash();

CREATE TRIGGER "audit_logs_reject_update"
BEFORE UPDATE ON "audit_logs"
FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();

CREATE TRIGGER "audit_logs_reject_delete"
BEFORE DELETE ON "audit_logs"
FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();

CREATE TRIGGER "audit_logs_reject_truncate"
BEFORE TRUNCATE ON "audit_logs"
FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_mutation();
