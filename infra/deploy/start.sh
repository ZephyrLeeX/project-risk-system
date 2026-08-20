#!/usr/bin/env bash
# Start the stack (or specific services) without rebuilding.
#
# Usage:
#   ./infra/deploy/start.sh
#   ./infra/deploy/start.sh api worker
#   ./infra/deploy/start.sh --help
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { cat <<'EOF'
Usage: start.sh [service...] [--help]

Starts services with `docker compose up -d` (no build, no volume removal).
With no args, starts the whole stack.
EOF
exit 0; }

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_project_root
require_env_file

log "starting stack"
compose up -d "$@"
ok "start issued (run 'infra/deploy/healthcheck.sh' to verify)"
