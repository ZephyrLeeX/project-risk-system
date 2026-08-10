-- Audit-log integrity, summaries and controlled export permission.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE "audit_logs"
  ADD COLUMN "summary" VARCHAR(500),
  ADD COLUMN "isSensitive" BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN "previousHash" VARCHAR(64),
  ADD COLUMN "integrityHash" VARCHAR(64);

UPDATE "audit_logs"
SET
  "summary" = LEFT(
    "action" || ' · ' || "resourceType" ||
    CASE WHEN "resourceId" IS NULL THEN '' ELSE '/' || "resourceId" END,
    500
  ),
  "isSensitive" = (
    "module" IN ('ADMIN_USER', 'ADMIN_ROLE', 'ADMIN_AI', 'SYSTEM_CONFIG', 'AUDIT')
    OR "action" ~* '(PASSWORD|KEY|PERMISSION|SCOPE|ROLLBACK|ROLLED_BACK|PUBLISH|EXPORT|STATUS)'
  );

CREATE OR REPLACE FUNCTION audit_log_compute_hash(
  p_id UUID,
  p_actor_user_id UUID,
  p_module TEXT,
  p_action TEXT,
  p_resource_type TEXT,
  p_resource_id TEXT,
  p_result TEXT,
  p_trace_id TEXT,
  p_client_ip TEXT,
  p_user_agent TEXT,
  p_before_snapshot JSONB,
  p_after_snapshot JSONB,
  p_error_code TEXT,
  p_summary TEXT,
  p_is_sensitive BOOLEAN,
  p_previous_hash TEXT,
  p_created_at TIMESTAMPTZ
) RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
AS $$
  SELECT encode(
    digest(
      concat_ws('|',
        p_id::text,
        COALESCE(p_actor_user_id::text, ''),
        p_module,
        p_action,
        p_resource_type,
        COALESCE(p_resource_id, ''),
        p_result,
        p_trace_id,
        COALESCE(p_client_ip, ''),
        COALESCE(p_user_agent, ''),
        COALESCE(p_before_snapshot::text, ''),
        COALESCE(p_after_snapshot::text, ''),
        COALESCE(p_error_code, ''),
        COALESCE(p_summary, ''),
        p_is_sensitive::text,
        COALESCE(p_previous_hash, ''),
        (EXTRACT(EPOCH FROM p_created_at) * 1000)::bigint::text
      ),
      'sha256'
    ),
    'hex'
  );
$$;

DO $$
DECLARE
  current_row RECORD;
  prior_hash TEXT := NULL;
  calculated_hash TEXT;
BEGIN
  FOR current_row IN SELECT * FROM "audit_logs" ORDER BY "createdAt", "id" LOOP
    calculated_hash := audit_log_compute_hash(
      current_row."id",
      current_row."actorUserId",
      current_row."module",
      current_row."action",
      current_row."resourceType",
      current_row."resourceId",
      current_row."result"::text,
      current_row."traceId",
      current_row."clientIp",
      current_row."userAgent",
      current_row."beforeSnapshot",
      current_row."afterSnapshot",
      current_row."errorCode",
      current_row."summary",
      current_row."isSensitive",
      prior_hash,
      current_row."createdAt"
    );
    UPDATE "audit_logs"
    SET "previousHash" = prior_hash, "integrityHash" = calculated_hash
    WHERE "id" = current_row."id";
    prior_hash := calculated_hash;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION audit_log_append_hash()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(918273645);
  SELECT "integrityHash"
  INTO NEW."previousHash"
  FROM "audit_logs"
  ORDER BY "createdAt" DESC, "id" DESC
  LIMIT 1;

  NEW."integrityHash" := audit_log_compute_hash(
    NEW."id",
    NEW."actorUserId",
    NEW."module",
    NEW."action",
    NEW."resourceType",
    NEW."resourceId",
    NEW."result"::text,
    NEW."traceId",
    NEW."clientIp",
    NEW."userAgent",
    NEW."beforeSnapshot",
    NEW."afterSnapshot",
    NEW."errorCode",
    NEW."summary",
    NEW."isSensitive",
    NEW."previousHash",
    NEW."createdAt"
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

CREATE INDEX "audit_logs_isSensitive_createdAt_idx"
  ON "audit_logs"("isSensitive", "createdAt");

INSERT INTO "permissions" ("id", "code", "name", "module", "description")
VALUES (
  gen_random_uuid(),
  'admin.audit.export',
  '导出审计日志',
  'ADMIN',
  '导出审计日志的系统权限点'
)
ON CONFLICT ("code") DO UPDATE SET
  "name" = EXCLUDED."name",
  "module" = EXCLUDED."module",
  "description" = EXCLUDED."description";

INSERT INTO "role_permissions" ("roleId", "permissionId")
SELECT role_record."id", permission_record."id"
FROM "roles" role_record
JOIN "permissions" permission_record
  ON permission_record."code" = 'admin.audit.export'
WHERE role_record."code" = 'SYSTEM_ADMIN'
ON CONFLICT ("roleId", "permissionId") DO NOTHING;
