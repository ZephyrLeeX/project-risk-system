#!/usr/bin/env bash
# Isolated restore drill wrapper over the approved T036 restore CLI.
#
# Restores an encrypted backup artifact into an ISOLATED, EMPTY target database
# + storage directory, then verifies integrity, the audit hash chain and file
# reconciliation (all performed by the existing risk_backup restore command).
# This NEVER restores into the live production database.
#
# Usage:
#   ./infra/deploy/restore-drill.sh \
#       --artifact /var/backups/risk/daily-20260815T120000Z.rpbk \
#       --target-db restore_drill \
#       --target-storage /var/tmp/restore-drill/storage \
#       --confirm-isolated
#   ./infra/deploy/restore-drill.sh --help
#
# A backup is valid only after a successful restore drill (ADR 0009).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

ARTIFACT=""
TARGET_DB=""
TARGET_STORAGE=""
CONFIRM=0
PREPARE=0
usage() {
  cat <<'EOF'
Usage: restore-drill.sh --artifact PATH --target-db NAME --target-storage DIR
                        --confirm-isolated [--prepare] [--help]

  --artifact PATH        Encrypted backup artifact (.rpbk) to restore.
  --target-db NAME       ISOLATED empty database name (must NOT equal the
                         production database). The script refuses the live DB.
  --target-storage DIR   ISOLATED empty directory for restored storage files.
                         Must be an absolute path; must NOT be the production
                         storage volume.
  --confirm-isolated     Required acknowledgement that the target is isolated
                         and NOT production.
  --prepare              Create the isolated target DB (empty) and storage dir
                         before restoring. Without this, both must already exist
                         and the DB must be empty.

This script is fail-closed: it will not run if the target database matches the
production database, or if the target storage is not an isolated empty path.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --artifact) ARTIFACT="${2:?--artifact requires a value}"; shift 2 ;;
    --target-db) TARGET_DB="${2:?--target-db requires a value}"; shift 2 ;;
    --target-storage) TARGET_STORAGE="${2:?--target-storage requires a value}"; shift 2 ;;
    --confirm-isolated) CONFIRM=1; shift ;;
    --prepare) PREPARE=1; shift ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_project_root
require_env_file

[[ -n "${ARTIFACT}" ]] || { usage; die "--artifact is required"; }
[[ -n "${TARGET_DB}" ]] || { usage; die "--target-db is required"; }
[[ -n "${TARGET_STORAGE}" ]] || { usage; die "--target-storage is required"; }
[[ -f "${ARTIFACT}" ]] || die "artifact not found: ${ARTIFACT}"
[[ "${TARGET_STORAGE}" == /* ]] || die "--target-storage must be an absolute path: ${TARGET_STORAGE}"
(( CONFIRM == 1 )) || die "refusing to run without --confirm-isolated (acknowledge the target is isolated, NOT production)"

# Read production DB name to refuse a live-target restore.
# shellcheck disable=SC1090
PROD_DB="$(set -a; . "$(env_file_path)"; set +a; printf '%s' "${POSTGRES_DB:-project_risk}")"
PROD_USER="$(set -a; . "$(env_file_path)"; set +a; printf '%s' "${POSTGRES_USER:-project_risk}")"
PROD_PASS="$(set -a; . "$(env_file_path)"; set +a; printf '%s' "${POSTGRES_PASSWORD:-}")"
[[ -n "${PROD_PASS}" ]] || die "POSTGRES_PASSWORD is empty in ${ENV_FILE}"

# --- fail-closed: refuse the live production database ------------------------
[[ "${TARGET_DB}" != "${PROD_DB}" ]] \
  || die "FAIL-CLOSED: --target-db '${TARGET_DB}' equals the production database '${PROD_DB}'. Restore drill must target an ISOLATED database."
[[ "${TARGET_DB}" != "postgres" && "${TARGET_DB}" != "template1" ]] \
  || die "FAIL-CLOSED: --target-db '${TARGET_DB}' is a system database; choose an isolated name like 'restore_drill'."
# Refuse paths that look like the production storage volume or the live mount.
case "${TARGET_STORAGE}" in
  /app/storage|/app/storage/*) die "FAIL-CLOSED: --target-storage must not be the live /app/storage path";;
esac

KEK_VERSION="${BACKUP_KEK_VERSION}"
KEK_FILE="${BACKUP_KEK_FILE}"
[[ -f "${KEK_FILE}" ]] \
  || die "backup KEK file not found: ${KEK_FILE} (set BACKUP_KEK_FILE in deploy.conf)"
KEK_DIR="$(cd "$(dirname "${KEK_FILE}")" && pwd)"
KEK_BASE="$(basename "${KEK_FILE}")"
[[ -f "${DOCKER_BIN}" ]] \
  || die "DOCKER_BIN not found: ${DOCKER_BIN} (set DOCKER_BIN in deploy.conf)"

ARTIFACT_DIR="$(cd "$(dirname "${ARTIFACT}")" && pwd)"
ARTIFACT_BASE="$(basename "${ARTIFACT}")"

# --- optionally prepare the isolated empty target ----------------------------
if (( PREPARE )); then
  log "preparing isolated target (creating empty DB '${TARGET_DB}' and storage dir)"
  compose exec -T postgres psql -U "${PROD_USER}" -d "${PROD_DB}" \
    -c "CREATE DATABASE \"${TARGET_DB}\";" \
    || die "could not create isolated database '${TARGET_DB}' (does it already exist? drop it first)"
  mkdir -p "${TARGET_STORAGE}"
  rm -rf "${TARGET_STORAGE:?}"/* "${TARGET_STORAGE}"/.[!.]* 2>/dev/null || true
else
  log "verifying isolated target already exists and is empty"
  compose exec -T postgres psql -U "${PROD_USER}" -d "${PROD_DB}" -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${TARGET_DB}';" \
    | grep -q 1 || die "isolated database '${TARGET_DB}' does not exist (pass --prepare to create it)"
  if [[ -d "${TARGET_STORAGE}" ]] && [[ -n "$(ls -A "${TARGET_STORAGE}" 2>/dev/null)" ]]; then
    die "target storage is not empty: ${TARGET_STORAGE} (pass --prepare to clean it, or empty it manually)"
  fi
  mkdir -p "${TARGET_STORAGE}"
fi
ok "isolated target ready: db='${TARGET_DB}', storage='${TARGET_STORAGE}'"

LOG_TMP="$(mktemp)"
trap 'rm -f "${LOG_TMP}"' EXIT

log "running isolated restore drill (artifact: ${ARTIFACT})"
RESTORE_EXIT=0
compose run --rm --no-deps --user 0:0 \
  -v "${ARTIFACT_DIR}:/backup:ro" \
  -v "${TARGET_STORAGE}:/drill/storage" \
  -v "${KEK_DIR}:/keys:ro" \
  -v "${PROJECT_ROOT}/infra/backup/src:/opt/risk_backup:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${DOCKER_BIN}:/usr/bin/docker:ro" \
  -e PYTHONPATH=/opt/risk_backup \
  api python -m risk_backup restore \
    --artifact "/backup/${ARTIFACT_BASE}" \
    --target-dsn "postgresql://${PROD_USER}:${PROD_PASS}@postgres:5432/${TARGET_DB}" \
    --pg-runner "docker exec -i project-risk-postgres" \
    --pg-socket-dir /var/run/postgresql \
    --pg-user "${PROD_USER}" --pg-db "${TARGET_DB}" \
    --target-storage-root /drill/storage \
    --temp-dir /tmp/risk-restore \
    --kek-version "${KEK_VERSION}" --kek-file "${KEK_VERSION}=/keys/${KEK_BASE}" 2> "${LOG_TMP}" || RESTORE_EXIT=$?

# --- report (integrity / audit chain / reconcile come from the CLI) ----------
REPORT="$(python3 - "${LOG_TMP}" <<'PY' 2>/dev/null || true
import json, sys
last = None
for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    if rec.get("event") == "restore":
        last = rec
if last:
    print("ok=" + str(last.get("ok")))
    print("backupId=" + str(last.get("backupId", "?")))
    print("auditTotal=" + str(last.get("auditTotalRecords", "?")))
    print("auditVerified=" + str(last.get("auditVerifiedRecords", "?")))
    print("errorCode=" + str(last.get("errorCode", "")))
    rc = last.get("reconcile") or {}
    print("reconcile_referenced=" + str(rc.get("referencedCount", "?")))
    print("reconcile_present=" + str(rc.get("presentCount", "?")))
    print("reconcile_orphans_removed=" + str(rc.get("orphansRemoved", "?")))
    print("reconcile_missing=" + str(rc.get("missingCount", "?")))
PY
)"

if (( RESTORE_EXIT == 0 )); then
  ok "restore drill SUCCEEDED (integrity + audit chain + reconciliation verified)"
  printf '%s\n' "${REPORT}" | sed 's/^/  /'
  printf '\nDrop the isolated target when done:\n'
  printf '  docker exec project-risk-postgres psql -U %s -d %s -c "DROP DATABASE %s;"\n' \
    "${PROD_USER}" "${PROD_DB}" "${TARGET_DB}"
  printf '  rm -rf %s\n' "${TARGET_STORAGE}"
  exit 0
else
  warn "restore drill FAILED (the backup did NOT validate against this isolated target)"
  printf '%s\n' "${REPORT}" | sed 's/^/  /' >&2
  printf '  metadata log (last lines):\n' >&2
  tail -n 5 "${LOG_TMP}" >&2 || true
  printf '\nA failed drill means the backup is NOT trusted. Investigate before\n' >&2
  printf 'relying on this artifact. The isolated target db/storage were not touched\n' >&2
  printf 'by production data (production database was never used).\n' >&2
  exit 1
fi
