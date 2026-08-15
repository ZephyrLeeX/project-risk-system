# shellcheck shell=bash
# Shared helpers for the single-server Docker Compose deployment kit.
#
# Every deployment script must `set -Eeuo pipefail` itself BEFORE sourcing this
# file (sourcing does not change the caller's options). This library only
# provides: project-root resolution, deploy.conf loading, a `compose` wrapper,
# logging, prerequisite checks, and a generic health-wait helper.
#
# No secret value is ever read, printed or exported here. Only non-secret paths
# and variable *names* from deploy.conf are loaded.

# Resolve the deployable project root (the directory containing `infra/`).
# Walk up from the script directory until `infra/docker-compose.yml` is found,
# so the result is correct regardless of how deep under <root>/infra/ this
# library or its caller lives.
deploy_root_init() {
  local dir
  if [[ -n "${SCRIPT_DIR:-}" ]]; then
    dir="${SCRIPT_DIR}"
  else
    dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  while [[ "${dir}" != "/" ]]; do
    if [[ -f "${dir}/infra/docker-compose.yml" ]]; then
      PROJECT_ROOT="${dir}"
      export PROJECT_ROOT
      return
    fi
    dir="$(cd "${dir}/.." && pwd)"
  done
  die "could not locate project root (infra/docker-compose.yml not found above ${SCRIPT_DIR:-${BASH_SOURCE[0]}})"
}

# Defaults for the non-secret deployment configuration. Operators override these
# by copying deploy.conf.example -> deploy.conf and editing it. No secret is a
# default and none is loaded from deploy.conf.
deploy_conf_defaults() {
  COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
  ENV_FILE="${ENV_FILE:-.env.production}"
  PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://risk.example.internal:8443}"
  PROXY_HTTPS_PORT="${PROXY_HTTPS_PORT:-8443}"
  WAIT_TIMEOUT="${WAIT_TIMEOUT:-300}"
  # Backup wrapper knobs (paths only, never key material).
  BACKUP_DIR="${BACKUP_DIR:-/var/backups/risk}"
  BACKUP_TYPE="${BACKUP_TYPE:-daily}"
  BACKUP_KEK_VERSION="${BACKUP_KEK_VERSION:-v1}"
  BACKUP_KEK_FILE="${BACKUP_KEK_FILE:-/etc/risk/backup-keys/backup_kek_v1}"
  # Host docker binary bind-mounted into the one-shot backup container so the
  # in-container orchestrator can run `docker exec -i project-risk-postgres`
  # for pg_dump/pg_restore (ADR 0031 §12). Standard Docker Engine path.
  DOCKER_BIN="${DOCKER_BIN:-/usr/bin/docker}"
}

# Load deploy.conf if present. Fail closed if it exists but is world-writable
# (a real deployment should not let any operator edit deployment config).
deploy_conf_load() {
  local conf="${PROJECT_ROOT}/infra/deploy/deploy.conf"
  if [[ -f "${conf}" ]]; then
    if [[ -n "$(find "${conf}" -perm -0002 -print 2>/dev/null)" ]]; then
      die "deploy.conf is world-writable; refuse to load: ${conf}"
    fi
    # shellcheck disable=SC1090
    . "${conf}"
  fi
  deploy_conf_defaults
}

# Absolute path to the compose file (PROJECT_ROOT-relative COMPOSE_FILE).
compose_file_path() {
  printf '%s/%s' "${PROJECT_ROOT}" "${COMPOSE_FILE}"
}

# env-file absolute path. Must exist for any compose command that interpolates
# the required POSTGRES_PASSWORD / DATA_ENCRYPTION_KEY / CORS_ORIGIN.
env_file_path() {
  printf '%s/%s' "${PROJECT_ROOT}" "${ENV_FILE}"
}

# Print the docker compose invocation prefix used by every script.
# Usage: compose <subcommand> [args...]
compose() {
  docker compose --env-file "$(env_file_path)" -f "$(compose_file_path)" "$@"
}

# --- logging -----------------------------------------------------------------

if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'
  C_BLU=$'\033[34m'; C_OFF=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YLW=''; C_BLU=''; C_OFF=''
fi

log()  { printf '%s==>%s %s\n' "${C_BLU}" "${C_OFF}" "$*"; }
ok()   { printf '%s[OK]%s %s\n'   "${C_GRN}" "${C_OFF}" "$*"; }
warn() { printf '%s[!!]%s %s\n'   "${C_YLW}" "${C_OFF}" "$*" >&2; }
die()  { printf '%s[FATAL]%s %s\n' "${C_RED}" "${C_OFF}" "$*" >&2; exit 1; }

# --- prerequisite checks -----------------------------------------------------

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1${2:+ ($2)}"
}

# Verify we are running inside the deployable project root (infra/ exists).
require_project_root() {
  [[ -f "$(compose_file_path)" ]] \
    || die "compose file not found: $(compose_file_path) (run from the project root that contains infra/)"
}

# Verify the env file exists and is not the unedited template.
require_env_file() {
  local ef
  ef="$(env_file_path)"
  [[ -f "${ef}" ]] \
    || die "env file not found: ${ef} (copy infra/env.example -> ${ENV_FILE} and fill real values)"
  grep -qE 'POSTGRES_PASSWORD=replace_with|DATA_ENCRYPTION_KEY=replace_with|INITIAL_ADMIN_PASSWORD=replace_with' "${ef}" \
    && die "env file still contains placeholder secret values: ${ef} (set POSTGRES_PASSWORD, DATA_ENCRYPTION_KEY, INITIAL_ADMIN_PASSWORD)" || true
}

# --- generic helpers ---------------------------------------------------------

# Wait until a command exits 0, up to WAIT_TIMEOUT seconds.
# Usage: wait_until <description> <command...>
wait_until() {
  local desc="$1"; shift
  local deadline=$(( SECONDS + WAIT_TIMEOUT ))
  local waited=0
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      warn "timed out after ${WAIT_TIMEOUT}s waiting for: ${desc}"
      return 1
    fi
    sleep 2; waited=$(( waited + 2 ))
    if (( waited % 10 == 0 )); then
      printf '   ...waiting for %s (%ss)\n' "${desc}" "${waited}" >&2
    fi
  done
  ok "ready: ${desc}"
}

# Run a single health probe and record pass/fail. Sets HEALTH_FAIL=1 on failure.
# Usage: probe <label> <command...>
probe() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    ok "${label}"
  else
    warn "FAIL: ${label}"
    HEALTH_FAIL=1
  fi
}

HEALTH_FAIL=0
