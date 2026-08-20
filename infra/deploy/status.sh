#!/usr/bin/env bash
# Show deployment status: deployed Git SHA, compose ps, per-service image,
# health summary, volumes and a small tail of recent logs.
#
# This script deliberately does NOT print environment values (no `compose config`,
# no `docker inspect` of Env). Secrets stay in their files/env only.
#
# Usage:
#   ./infra/deploy/status.sh
#   ./infra/deploy/status.sh --help
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { cat <<'EOF'
Usage: status.sh [--help]

Prints: deployed SHA, compose ps, image per service, health summary,
named volumes, and the last few log lines of api/worker/scheduler/proxy.
No secret environment values are printed.
EOF
exit 0; }

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_cmd git

section() { printf '\n%s=== %s ===%s\n' "${C_BLU}" "$*" "${C_OFF}"; }

# --- deployed SHA ------------------------------------------------------------
section "deployed release"
DEPLOYED_SHA_FILE="${PROJECT_ROOT}/infra/deploy/.deployed-sha"
if [[ -f "${DEPLOYED_SHA_FILE}" ]]; then
  printf 'recorded deployed SHA: %s\n' "$(cat "${DEPLOYED_SHA_FILE}")"
else
  printf 'recorded deployed SHA: (none; run deploy.sh / update.sh first)\n'
fi
printf 'current working tree SHA: %s\n' "$(git rev-parse HEAD 2>/dev/null || echo '(not a git repo)')"
printf 'working tree: '
git status --porcelain | grep -q . && printf 'dirty\n' || printf 'clean\n'

# --- compose ps --------------------------------------------------------------
section "compose ps (compose file: $(compose_file_path))"
compose ps || true

# --- image per service -------------------------------------------------------
section "service images"
# Container names differ from service names (redis -> project-risk-redis-app).
for c in project-risk-postgres project-risk-redis-app project-risk-api \
         project-risk-worker project-risk-scheduler project-risk-web project-risk-proxy; do
  img="$(docker inspect --format '{{.Config.Image}}' "${c}" 2>/dev/null || echo '(not running)')"
  state="$(docker inspect --format '{{.State.Status}}' "${c}" 2>/dev/null || echo '-')"
  printf '  %-28s %-26s %s\n' "${c}" "${img}" "${state}"
done

# --- health summary ----------------------------------------------------------
section "health summary"
if "${SCRIPT_DIR}/healthcheck.sh" >/dev/null 2>&1; then
  ok "HEALTHCHECK_OK"
else
  warn "HEALTHCHECK_FAILED (run './infra/deploy/healthcheck.sh' for details)"
fi

# --- volumes -----------------------------------------------------------------
section "named volumes"
docker volume ls --filter name=project-risk- 2>/dev/null || true

# --- recent logs -------------------------------------------------------------
section "recent logs (last 15 lines each)"
for svc in api worker scheduler proxy; do
  printf '\n--- %s ---\n' "${svc}"
  compose logs --tail=15 "${svc}" 2>/dev/null || true
done

printf '\n'
ok "status complete (no secret values printed)"
