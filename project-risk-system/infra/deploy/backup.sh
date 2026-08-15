#!/usr/bin/env bash
# Thin wrapper over the approved T036 backup CLI (infra/backup/**).
#
# It does NOT reimplement encryption, quiesce logic or integrity checks. It only
# sequences the approved runbook: quiesce write paths -> run the one-shot
# encrypted backup orchestrator in the api image -> unquiesce, and report the
# backup id / status / artifact location. Exits non-zero if the backup is not
# USABLE.
#
# Usage:
#   ./infra/deploy/backup.sh
#   ./infra/deploy/backup.sh --type weekly
#   ./infra/deploy/backup.sh --output /var/backups/risk/manual.rpbk
#   ./infra/deploy/backup.sh --help
#
# The backup KEK is loaded from a host read-only file (BACKUP_KEK_FILE). Its
# contents are NEVER read or printed by this script.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

BACKUP_TYPE_ARG=""
BACKUP_OUTPUT_ARG=""
usage() {
  cat <<'EOF'
Usage: backup.sh [--type daily|weekly|monthly] [--output PATH] [--help]

  --type     Backup type (default: from deploy.conf BACKUP_TYPE, else 'daily').
  --output   Artifact path on the host (default: <BACKUP_DIR>/<type>-<utc-time>.rpbk).

Reads from deploy.conf: BACKUP_DIR, BACKUP_KEK_VERSION, BACKUP_KEK_FILE, DOCKER_BIN.
Reads from .env.production: POSTGRES_USER, POSTGRES_DB, POSTGRES_PASSWORD (for the
backup DSN; never printed).

Prerequisites:
  - BACKUP_KEK_FILE exists and is readable (host-only, 0400; generate with
    `openssl rand -base64 32 > /etc/risk/backup-keys/backup_kek_v1; chmod 0400 ...`).
  - DOCKER_BIN exists (default /usr/bin/docker) — bind-mounted into the one-shot
    backup container so the in-container orchestrator can run pg_dump/pg_restore
    inside the postgres container (ADR 0031 §12).
  - The stack is running (postgres + the write-path services to be quiesced).

Security note: the backup container runs as root with the docker socket mounted
(equivalent to host root). Run backup only as a trusted operator; it is a
one-shot, manual operation.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --type) BACKUP_TYPE_ARG="${2:?--type requires a value}"; shift 2 ;;
    --output) BACKUP_OUTPUT_ARG="${2:?--output requires a value}"; shift 2 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_project_root
require_env_file

BTYPE="${BACKUP_TYPE_ARG:-${BACKUP_TYPE}}"
[[ -n "${BTYPE}" ]] || BTYPE="daily"
case "${BTYPE}" in
  daily|weekly|monthly) ;;
  *) die "invalid backup type '${BTYPE}' (expected daily|weekly|monthly)" ;;
esac

KEK_VERSION="${BACKUP_KEK_VERSION}"
KEK_FILE="${BACKUP_KEK_FILE}"
[[ -f "${KEK_FILE}" ]] \
  || die "backup KEK file not found: ${KEK_FILE} (set BACKUP_KEK_FILE in deploy.conf; generate with 'openssl rand -base64 32 > ${KEK_FILE}; chmod 0400 ${KEK_FILE}')"
KEK_DIR="$(cd "$(dirname "${KEK_FILE}")" && pwd)"
KEK_BASE="$(basename "${KEK_FILE}")"

[[ -d "${BACKUP_DIR}" ]] \
  || die "BACKUP_DIR does not exist: ${BACKUP_DIR} (create it first: 'mkdir -p ${BACKUP_DIR}')"
BACKUP_DIR_ABS="$(cd "${BACKUP_DIR}" && pwd)"

[[ -f "${DOCKER_BIN}" ]] \
  || die "DOCKER_BIN not found: ${DOCKER_BIN} (set DOCKER_BIN in deploy.conf to your host docker binary, e.g. /usr/bin/docker)"

# Read Postgres credentials from the env file WITHOUT printing them. Sourced in
# a subshell so they never leak into the calling environment.
read -r PG_USER PG_DB PG_PASS < <(
  # shellcheck disable=SC1090
  set -a; . "$(env_file_path)"; set +a
  printf '%s\0%s\0%s' "${POSTGRES_USER:-project_risk}" "${POSTGRES_DB:-project_risk}" "${POSTGRES_PASSWORD:-}"
)
[[ -n "${PG_PASS}" ]] || die "POSTGRES_PASSWORD is empty in ${ENV_FILE}"

OUTPUT_HOST="${BACKUP_OUTPUT_ARG:-${BACKUP_DIR_ABS}/${BTYPE}-$(date -u +%Y%m%dT%H%M%SZ).rpbk}"
OUTPUT_CONTAINER="/backup/$(basename "${OUTPUT_HOST}")"

# A temporary file for the metadata-only JSON log (stderr). No keys are logged.
LOG_TMP="$(mktemp)"
trap 'rm -f "${LOG_TMP}"' EXIT

log "backup type=${BTYPE}"
log "artifact: ${OUTPUT_HOST}"
log "kek version: ${KEK_VERSION} (file: ${KEK_FILE}; contents not read)"

# --- 1. quiesce write paths (postgres + redis stay up) -----------------------
log "quiescing write paths (api worker scheduler)"
compose stop api worker scheduler >/dev/null \
  || die "quiesce failed (docker compose stop api worker scheduler)"

# --- 2. run the approved one-shot backup orchestrator ------------------------
# Mirrors infra/backup/README.md production invocation. The api image provides
# risk_platform.shared.crypto + risk_backup (mounted) + cryptography; pg_dump /
# pg_restore run inside the postgres container via the docker socket. Runs as
# root so the mounted docker socket is reachable.
BACKUP_EXIT=0
compose run --rm --no-deps --user 0:0 \
  -v project-risk-storage:/app/storage:ro \
  -v "${BACKUP_DIR_ABS}:/backup" \
  -v "${KEK_DIR}:/keys:ro" \
  -v "${PROJECT_ROOT}/infra/backup/src:/opt/risk_backup:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${DOCKER_BIN}:/usr/bin/docker:ro" \
  -e PYTHONPATH=/opt/risk_backup \
  api python -m risk_backup backup \
    --type "${BTYPE}" \
    --dsn "postgresql://${PG_USER}:${PG_PASS}@postgres:5432/${PG_DB}" \
    --pg-runner "docker exec -i project-risk-postgres" \
    --pg-socket-dir /var/run/postgresql \
    --pg-user "${PG_USER}" --pg-db "${PG_DB}" \
    --storage-root /app/storage \
    --output "${OUTPUT_CONTAINER}" \
    --temp-dir /tmp/risk-backup \
    --kek-version "${KEK_VERSION}" --kek-file "${KEK_VERSION}=/keys/${KEK_BASE}" \
    --quiesce none 2> "${LOG_TMP}" || BACKUP_EXIT=$?

# --- 3. unquiesce (always attempt, even on backup failure) -------------------
log "unquiescing write paths (api worker scheduler)"
compose up -d --no-deps api worker scheduler >/dev/null \
  || warn "unquiesce failed — restart manually: 'infra/deploy/start.sh api worker scheduler'"

# --- 4. report ---------------------------------------------------------------
# Parse the metadata-only log for the final backup record (backupId/status).
# Never parses or prints key material (the CLI logs only metadata: ADR 0031 §10).
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
    if rec.get("event") == "backup":
        last = rec
if last:
    print("backupId=" + str(last.get("backupId", "?")))
    print("status=" + str(last.get("status", "?")))
    print("errorCode=" + str(last.get("errorCode", "")))
PY
)"
bid=""; st=""; err=""
while IFS='=' read -r k v; do
  case "$k" in
    backupId) bid="$v" ;;
    status) st="$v" ;;
    errorCode) err="$v" ;;
  esac
done <<< "${REPORT}"

if (( BACKUP_EXIT == 0 )) && [[ -f "${OUTPUT_HOST}" ]]; then
  ok "backup USABLE"
  printf '  backupId: %s\n' "${bid:-?}"
  printf '  status:   %s\n' "${st:-?}"
  printf '  artifact: %s\n' "${OUTPUT_HOST}"
  printf '  size:     %s bytes\n' "$(stat -c %s "${OUTPUT_HOST}" 2>/dev/null || echo '?')"
  printf '\nRemember: a backup is only valid after a successful restore drill\n'
  printf '(see infra/deploy/restore-drill.sh on an ISOLATED target).\n'
  exit 0
else
  warn "backup NOT usable (exit=${BACKUP_EXIT}, status=${st:-?}, errorCode=${err:-none})"
  [[ -f "${OUTPUT_HOST}" ]] \
    && warn "artifact file exists but is NOT usable — do NOT trust it: ${OUTPUT_HOST}"
  printf '  metadata log (last lines):\n' >&2
  tail -n 5 "${LOG_TMP}" >&2 || true
  exit 1
fi
