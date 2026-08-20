#!/usr/bin/env bash
# Tail or dump logs for one or more services via `docker compose logs`.
#
# Usage:
#   ./infra/deploy/logs.sh                 # all services, follow
#   ./infra/deploy/logs.sh api             # one service, follow
#   ./infra/deploy/logs.sh worker scheduler
#   ./infra/deploy/logs.sh api --tail=200  # extra compose flags pass through
#   ./infra/deploy/logs.sh --help
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

usage() {
  cat <<'EOF'
Usage: logs.sh [service...] [-- tail/compose flags]

  service   One of: postgres redis api worker scheduler web proxy
            (default: all services). Unknown args are passed to `docker compose logs`.

Examples:
  logs.sh
  logs.sh api
  logs.sh api --tail=200 --no-log-prefix
EOF
}

case "${1:-}" in
  --help|-h) usage; exit 0 ;;
esac

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_project_root

KNOWN="postgres redis api worker scheduler web proxy"
services=()
passthrough=()
for arg in "$@"; do
  if [[ " ${KNOWN} " == *" ${arg} "* ]]; then
    services+=("${arg}")
  else
    passthrough+=("${arg}")
  fi
done

if (( ${#services[@]} == 0 )); then
  log "tailing all services (Ctrl-C to stop)"
  compose logs -f "${passthrough[@]:-}"
  exit $?
fi

log "tailing: ${services[*]} (Ctrl-C to stop)"
compose logs -f "${services[@]}" "${passthrough[@]:-}"
exit $?
