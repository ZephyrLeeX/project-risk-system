#!/usr/bin/env bash
# Unified health check for the single-server stack. Verifies every service and
# the critical integrations, then prints exactly `HEALTHCHECK_OK` on success or
# exits non-zero on any failure. Never returns 0 when a service is unhealthy.
#
# Usage:
#   ./infra/deploy/healthcheck.sh
#   ./infra/deploy/healthcheck.sh --help
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { cat <<'EOF'
Usage: healthcheck.sh [--help]

Checks: compose service status, postgres, redis, FastAPI /api/health,
proxy -> /api/health, proxy -> frontend, celery worker ping,
scheduler liveness + lock_held, required volume mounts.

Prints HEALTHCHECK_OK and exits 0 only if every probe passes.
EOF
exit 0; }

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_cmd curl "needed for proxy probes"

PG_USER="${POSTGRES_USER:-project_risk}"
PG_DB="${POSTGRES_DB:-project_risk}"
PORT="${PROXY_HTTPS_PORT:-8443}"
HEALTH_FAIL=0

log "healthcheck (proxy https port ${PORT})"

# --- compose service status --------------------------------------------------
SERVICE_UP_FAIL=0
for svc in postgres redis api worker scheduler web proxy; do
  cid="$(compose ps -q "${svc}" 2>/dev/null || true)"
  if [[ -z "${cid}" ]]; then
    warn "service not running: ${svc}"
    SERVICE_UP_FAIL=1
  fi
done
if (( SERVICE_UP_FAIL == 0 )); then ok "all compose services running"; else HEALTH_FAIL=1; fi

# --- postgres ----------------------------------------------------------------
probe "postgres healthy (pg_isready)" \
  compose exec -T postgres pg_isready -U "${PG_USER}" -d "${PG_DB}"

# --- redis -------------------------------------------------------------------
probe "redis PONG" \
  compose exec -T redis redis-cli ping

# --- FastAPI /api/health (in-container, bypasses proxy) ----------------------
probe "api /api/health" \
  compose exec -T api python -c 'import sys,urllib.request;sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:3000/api/health",timeout=3).status==200 else 1)'

# --- proxy -> /api/health (end-to-end through TLS reverse proxy) -------------
probe "proxy -> /api/health" \
  curl -fsSk --max-time 5 "https://127.0.0.1:${PORT}/api/health"

# --- proxy -> frontend -------------------------------------------------------
probe "proxy -> frontend (200)" \
  bash -c 'code="$(curl -sSk -o /dev/null -w "%{http_code}" --max-time 5 "https://127.0.0.1:'"${PORT}"'/")"; [[ "${code}" == 200 || "${code}" == 301 || "${code}" == 302 ]]'

# --- celery worker ping ------------------------------------------------------
probe "celery worker ping (celery@worker1)" \
  compose exec -T worker celery -A risk_platform.reliability.celery_app:celery_app inspect ping -d celery@worker1 --timeout=5

# --- scheduler liveness + lock_held -----------------------------------------
# The ADR 0030 liveness probe serves a JSON snapshot at :9191 (root path) and
# returns HTTP 503 when unhealthy. We require both healthy=true and
# lock_held=true (single-active: lock_held=false means a second scheduler is
# active or the lock was lost -> fail-closed).
probe "scheduler liveness healthy=true" \
  compose exec -T scheduler python -c 'import sys,urllib.request,json
try:
    r=urllib.request.urlopen("http://127.0.0.1:9191/",timeout=3)
    d=json.load(r)
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("healthy") is True else 1)'

probe "scheduler single-active lock_held=true" \
  compose exec -T scheduler python -c 'import sys,urllib.request,json
try:
    r=urllib.request.urlopen("http://127.0.0.1:9191/",timeout=3)
    d=json.load(r)
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("lock_held") is True else 1)'

# --- required volume mounts --------------------------------------------------
probe "volume exists: project-risk-postgres-data" \
  bash -c 'docker volume inspect project-risk-postgres-data >/dev/null 2>&1'
probe "volume exists: project-risk-storage" \
  bash -c 'docker volume inspect project-risk-storage >/dev/null 2>&1'
probe "postgres mounts data volume" \
  bash -c 'docker inspect --format "{{range .Mounts}}{{.Name}}{{println}}{{end}}" project-risk-postgres 2>/dev/null | grep -q project-risk-postgres-data'
probe "api mounts storage volume" \
  bash -c 'docker inspect --format "{{range .Mounts}}{{.Name}}{{println}}{{end}}" project-risk-api 2>/dev/null | grep -q project-risk-storage'

# --- verdict -----------------------------------------------------------------
if (( HEALTH_FAIL == 0 )); then
  printf 'HEALTHCHECK_OK\n'
  exit 0
else
  printf 'HEALTHCHECK_FAILED\n' >&2
  exit 1
fi
