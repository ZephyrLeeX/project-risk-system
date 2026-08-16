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

# Absolute path to the first compose file (PROJECT_ROOT-relative COMPOSE_FILE).
# COMPOSE_FILE may be a colon-separated Compose file list, matching Docker
# Compose's standard environment-variable syntax. The deployment wrapper passes
# each entry as its own `-f` argument, which Docker Compose requires when its
# files are supplied on the command line.
compose_file_path() {
  local first="${COMPOSE_FILE%%:*}"
  printf '%s/%s' "${PROJECT_ROOT}" "${first}"
}

compose_file_args() {
  local entry
  local -a entries=()
  IFS=':' read -r -a entries <<< "${COMPOSE_FILE}"
  for entry in "${entries[@]}"; do
    [[ -n "${entry}" ]] || die "COMPOSE_FILE contains an empty path: ${COMPOSE_FILE}"
    printf '%s\0' "${PROJECT_ROOT}/${entry}"
  done
}

# env-file absolute path. Must exist for any compose command that interpolates
# the required POSTGRES_PASSWORD / DATA_ENCRYPTION_KEY / CORS_ORIGIN.
env_file_path() {
  printf '%s/%s' "${PROJECT_ROOT}" "${ENV_FILE}"
}

# Print the docker compose invocation prefix used by every script.
# Usage: compose <subcommand> [args...]
compose() {
  local -a file_args=()
  while IFS= read -r -d '' file; do
    file_args+=( -f "${file}" )
  done < <(compose_file_args)
  docker compose --env-file "$(env_file_path)" "${file_args[@]}" "$@"
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
  local file
  while IFS= read -r -d '' file; do
    [[ -f "${file}" ]] \
      || die "compose file not found: ${file} (run from the project root that contains infra/)"
  done < <(compose_file_args)
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

# --- one-shot api commands (migration / seed) --------------------------------

# Read a single value from the interpolation env file by key name, without
# printing it. Strips one layer of matching surrounding quotes exactly like
# compose does when interpolating. Returns 1 if the key is absent or empty.
# Usage: env_file_value <NAME>
env_file_value() {
  local name="$1" line
  line="$(grep -E "^${name}=" "$(env_file_path)" 2>/dev/null | tail -n1)" || return 1
  line="${line#*=}"
  if { [[ "${line:0:1}" == '"' && "${line: -1}" == '"' ]] \
       || [[ "${line:0:1}" == "'" && "${line: -1}" == "'" ]]; }; then
    line="${line:1:${#line}-2}"
  fi
  [[ -n "${line}" ]] || return 1
  printf '%s' "${line}"
}

# Run a one-shot command in the api image WITHOUT requiring a long-running api
# container. `compose run --rm --no-deps -T` creates a throwaway container from
# the api service definition (same image, env, secrets, volumes and network as
# the service) and removes it on exit. Dependencies are never started by this
# helper — callers wait on postgres/redis health themselves beforehand.
#
# The command's own exit code is authoritative: no HTTP health probe (e.g. a
# 503 "unhealthy" state) is consulted, and no dependency is restarted.
#
# Env injection (values are read from ${ENV_FILE} and NEVER echoed):
#   --env NAME             pass NAME into the container; die if missing/empty.
#   --env-if-present NAME  pass NAME if present in ${ENV_FILE}, else skip.
#
# Usage: compose_run_api [--env NAME | --env-if-present NAME]... <command...>
compose_run_api() {
  local -a run_opts=()
  local flag name val
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env|--env-if-present)
        flag="$1"; name="${2:-}"
        [[ -n "${name}" ]] || die "compose_run_api: ${flag} needs a variable name"
        if val="$(env_file_value "${name}")"; then
          run_opts+=( -e "${name}=${val}" )
        elif [[ "${flag}" == "--env" ]]; then
          die "env file '${ENV_FILE}' is missing required '${name}' (needed by one-shot api command)"
        fi
        shift 2 ;;
      --) shift; break ;;
      *) break ;;
    esac
  done
  compose run --rm --no-deps -T "${run_opts[@]}" api "$@"
}

# Verify the database is migrated to the alembic head. Compares the offline
# `alembic heads` revision against the database's current revision, both via
# one-shot api containers (never `exec`, never an HTTP probe). Dies if the
# database is not at head — e.g. a fresh database with no alembic_version table.
verify_migration_head() {
  local expected actual
  expected="$(compose run --rm --no-deps -T api alembic heads 2>/dev/null | awk '/\(head\)/{print $1; exit}' || true)"
  actual="$(compose run --rm --no-deps -T api alembic current 2>/dev/null | awk '/\(head\)/{print $1; exit}' || true)"
  if [[ -n "${expected}" && "${actual}" == "${expected}" ]]; then
    ok "migration verified: database at head ${actual}"
  else
    die "migration verification FAILED (expected head ${expected:-unknown}, database reports ${actual:-no revision})"
  fi
}
