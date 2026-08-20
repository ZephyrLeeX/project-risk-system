#!/usr/bin/env bash
# Restart the stack (or specific services) without rebuilding or removing volumes.
#
# Usage:
#   ./infra/deploy/restart.sh
#   ./infra/deploy/restart.sh api worker
#   ./infra/deploy/restart.sh --help
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { cat <<'EOF'
Usage: restart.sh [service...] [--help]

Restarts services with `docker compose restart` (no rebuild, no volume removal).
With no args, restarts the whole stack.
EOF
exit 0; }

for arg in "$@"; do
  case "${arg}" in
    -v|--volumes) die "refusing --volumes: restart.sh never removes volumes" ;;
  esac
done

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_project_root

log "restarting stack"
compose restart "$@"
ok "restart issued (run 'infra/deploy/healthcheck.sh' to verify)"
