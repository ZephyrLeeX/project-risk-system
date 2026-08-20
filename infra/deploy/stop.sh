#!/usr/bin/env bash
# Stop the stack (or specific services). NEVER removes volumes.
#
# Usage:
#   ./infra/deploy/stop.sh
#   ./infra/deploy/stop.sh api worker
#   ./infra/deploy/stop.sh --help
#
# This script will refuse any `-v`/`--volumes` flag. To remove volumes you must
# do it by hand with full awareness of the data loss (see the deployment
# tutorial §15). `docker compose down -v` is intentionally NOT used here.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

usage() {
  cat <<'EOF'
Usage: stop.sh [service...] [--help]

Stops services with `docker compose stop` (containers kept; volumes untouched).
With no args, stops the whole stack. Volumes are NEVER removed by this script.
EOF
}

for arg in "$@"; do
  case "${arg}" in
    --help|-h) usage; exit 0 ;;
    -v|--volumes) die "refusing --volumes: stop.sh never removes volumes (use 'docker volume rm' by hand only, with data-loss awareness)" ;;
  esac
done

cd "${PROJECT_ROOT}"
require_cmd docker "Docker Engine"
require_project_root

log "stopping stack (volumes preserved)"
compose stop "$@"
ok "stopped (volumes intact; run 'infra/deploy/start.sh' to resume)"
