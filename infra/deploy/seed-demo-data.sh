#!/usr/bin/env bash
# Load only synthetic INTERNAL_MVP business demo data into the current Compose PostgreSQL.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

if [[ "${1:-}" != "--confirm-demo-data" || "$#" -ne 1 ]]; then
  cat >&2 <<'EOF'
Refusing to seed demo business data without an explicit confirmation.

Usage: ./infra/deploy/seed-demo-data.sh --confirm-demo-data
EOF
  exit 2
fi

require_cmd docker "Docker Engine"
docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin not found"
require_project_root
require_env_file
cd "${PROJECT_ROOT}"

log "loading synthetic WSLDEMO business data through the Compose api image"
compose_run_api -- risk-platform-demo-seed
ok "synthetic demo data loaded (idempotent; existing non-WSLDEMO data untouched)"
