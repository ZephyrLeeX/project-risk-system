#!/usr/bin/env bash
# Deploy a specific, pinned release onto an already-running single-server stack.
#
# This bridge-aware version supports the one-time repository layout migration
# from project-risk-system/ to the repository root. For ordinary releases it
# behaves like the existing updater. When the target release uses the new root
# layout, it preserves ignored production state, checks out the target, copies
# that state to the new root without overwriting anything, then hands control to
# the target release's infra/deploy/update.sh.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

usage() {
  cat <<'USAGE'
Usage: update.sh <git-tag-or-commit> [--no-backup] [--help]

  <git-tag-or-commit>   Pinned release to deploy. Production must never deploy
                        an unpinned branch HEAD — pass an explicit tag or SHA.
  --no-backup           Skip the suggested pre-update backup (NOT recommended).
  --help                Show this help.

Repository-layout migration:
  This bridge release can update from the historical project-risk-system/
  layout to a release whose deployable project lives at the repository root.
  Ignored production state is copied (never overwritten) before the target
  updater resumes the deployment.

Rollback boundary:
  Code can be rolled back by checking out the previous SHA. Database recovery
  must use the approved backup/restore runbook (infra/deploy/restore-drill.sh
  on an isolated target). Automatic Alembic downgrade is NOT performed and is
  not assumed safe.
USAGE
}

NO_BACKUP=0
TARGET=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --no-backup) NO_BACKUP=1; shift ;;
    -*) die "unknown option: $1 (try --help)" ;;
    *)
      [[ -n "${TARGET}" ]] && die "only one <git-tag-or-commit> allowed"
      TARGET="$1"; shift ;;
  esac
done
[[ -n "${TARGET}" ]] || { usage; die "missing <git-tag-or-commit>"; }

cd "${PROJECT_ROOT}"
require_cmd git
require_cmd docker "Docker Engine"
require_project_root
require_env_file

REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
LEGACY_PROJECT_ROOT="${PROJECT_ROOT}"

copy_state_file() {
  local source_path="$1" destination_path="$2"
  [[ -e "${source_path}" || -L "${source_path}" ]] || return 0
  [[ ! -e "${destination_path}" && ! -L "${destination_path}" ]] \
    || die "layout migration refuses to overwrite existing state: ${destination_path}"
  mkdir -p "$(dirname "${destination_path}")"
  cp -a -- "${source_path}" "${destination_path}"
}

copy_state_directory() {
  local source_dir="$1" destination_dir="$2" relative source_path destination_path
  [[ -d "${source_dir}" ]] || return 0

  while IFS= read -r -d '' source_path; do
    relative="${source_path#${source_dir}/}"
    destination_path="${destination_dir}/${relative}"
    [[ ! -e "${destination_path}" && ! -L "${destination_path}" ]] \
      || die "layout migration refuses to overwrite existing state: ${destination_path}"
  done < <(find "${source_dir}" -mindepth 1 \( -type f -o -type l \) -print0)

  while IFS= read -r -d '' source_path; do
    relative="${source_path#${source_dir}/}"
    mkdir -p "${destination_dir}/${relative}"
  done < <(find "${source_dir}" -mindepth 1 -type d -print0)

  while IFS= read -r -d '' source_path; do
    relative="${source_path#${source_dir}/}"
    destination_path="${destination_dir}/${relative}"
    mkdir -p "$(dirname "${destination_path}")"
    cp -a -- "${source_path}" "${destination_path}"
  done < <(find "${source_dir}" -mindepth 1 \( -type f -o -type l \) -print0)
}

validate_migratable_env_path() {
  local path="$1" part
  local -a parts=()

  [[ -n "${path}" ]] || die "ENV_FILE must not be empty during layout migration"
  [[ "${path}" != /* ]] \
    || die "layout migration requires ENV_FILE to be project-relative: ${path}"

  IFS='/' read -r -a parts <<< "${path}"
  for part in "${parts[@]}"; do
    [[ -n "${part}" && "${part}" != "." && "${part}" != ".." ]] \
      || die "layout migration refuses unsafe ENV_FILE path: ${path}"
  done
}

migrate_ignored_deploy_state() {
  local target_root="$1"
  [[ "${LEGACY_PROJECT_ROOT}" != "${target_root}" ]] || return 0

  validate_migratable_env_path "${ENV_FILE}"

  log "copying ignored deployment state to new repository root"
  copy_state_file "${LEGACY_PROJECT_ROOT}/${ENV_FILE}" "${target_root}/${ENV_FILE}"
  copy_state_file "${LEGACY_PROJECT_ROOT}/infra/deploy/deploy.conf" "${target_root}/infra/deploy/deploy.conf"
  copy_state_file "${LEGACY_PROJECT_ROOT}/infra/deploy/.deployed-sha" "${target_root}/infra/deploy/.deployed-sha"
  copy_state_directory "${LEGACY_PROJECT_ROOT}/infra/secrets" "${target_root}/infra/secrets"
  copy_state_directory "${LEGACY_PROJECT_ROOT}/infra/proxy/certs" "${target_root}/infra/proxy/certs"
  ok "ignored deployment state copied; legacy copies retained for rollback"
}

# --- 1. working tree must be clean ------------------------------------------
log "checking working tree is clean"
if [[ -n "$(git status --porcelain)" ]]; then
  git status --short >&2
  die "working tree not clean; commit or stash before updating (refusing to deploy over uncommitted changes)"
fi

# --- 2. fetch + resolve target ----------------------------------------------
log "fetching origin"
git fetch --tags origin || die "git fetch failed (network/remote?)"

log "resolving target: ${TARGET}"
TARGET_SHA="$(git rev-parse --verify "${TARGET}^{commit}" 2>/dev/null || true)"
[[ -n "${TARGET_SHA}" ]] \
  || die "target does not resolve to a commit: ${TARGET}"
log "target resolved to: ${TARGET_SHA} ($(git log -1 --format='%s' "${TARGET_SHA}"))"

TARGET_USES_ROOT_LAYOUT=0
if git cat-file -e "${TARGET_SHA}:infra/deploy/update.sh" 2>/dev/null \
  && git cat-file -e "${TARGET_SHA}:apps/api/pyproject.toml" 2>/dev/null; then
  TARGET_USES_ROOT_LAYOUT=1
fi

# --- 3. record previous (deployed) SHA --------------------------------------
PREV_SHA="$(git rev-parse HEAD)"
DEPLOYED_SHA_FILE="${PROJECT_ROOT}/infra/deploy/.deployed-sha"
if [[ -f "${DEPLOYED_SHA_FILE}" ]]; then
  RECORDED_DEPLOYED="$(cat "${DEPLOYED_SHA_FILE}" 2>/dev/null || true)"
  [[ -n "${RECORDED_DEPLOYED}" ]] && PREV_SHA="${RECORDED_DEPLOYED}"
fi
log "previous deployed SHA: ${PREV_SHA}"

if [[ "${PREV_SHA}" == "${TARGET_SHA}" ]]; then
  warn "target SHA equals current deployed SHA; nothing to update"
  exit 0
fi

# --- 4. optional pre-update backup ------------------------------------------
if (( NO_BACKUP )); then
  warn "skipping pre-update backup (--no-backup)"
else
  log "suggested pre-update backup (recommended)"
  if [[ -x "${SCRIPT_DIR}/backup.sh" ]]; then
    if "${SCRIPT_DIR}/backup.sh"; then
      ok "pre-update backup completed"
    else
      die "pre-update backup FAILED; aborting update (re-run with --no-backup to skip, not recommended)"
    fi
  else
    warn "backup.sh not executable; skipping (ensure you have a recent backup)"
  fi
fi

# --- 5. checkout pinned SHA --------------------------------------------------
log "checking out pinned release: ${TARGET_SHA}"
git checkout "${TARGET_SHA}" || die "git checkout failed for ${TARGET_SHA}"

if (( TARGET_USES_ROOT_LAYOUT )); then
  migrate_ignored_deploy_state "${REPOSITORY_ROOT}"
  TARGET_UPDATE="${REPOSITORY_ROOT}/infra/deploy/update.sh"
  [[ -x "${TARGET_UPDATE}" ]] \
    || die "target root-layout updater is missing or not executable: ${TARGET_UPDATE}"
  log "handing deployment to target root-layout updater"
  export RISK_UPDATE_BRIDGE_TARGET_SHA="${TARGET_SHA}"
  export RISK_UPDATE_BRIDGE_PREV_SHA="${PREV_SHA}"
  exec "${TARGET_UPDATE}"
fi

# --- 6. compose config -------------------------------------------------------
log "validating compose config"
compose config --quiet || die "docker compose config failed after checkout"

# --- 7. rebuild images -------------------------------------------------------
log "rebuilding production images"
compose build --pull api web || die "image rebuild failed"

# --- 8. migrate --------------------------------------------------------------
log "applying alembic migrations (upgrade head only; no downgrade)"
compose up -d --no-deps postgres redis >/dev/null 2>&1 || true
compose_run_api alembic upgrade head \
  || die "alembic upgrade head failed after upgrade — DO NOT downgrade blindly; see rollback note below"
verify_migration_head

# --- 9. recreate stack -------------------------------------------------------
log "recreating stack"
compose up -d || die "failed to recreate stack"

# --- 10. healthcheck ---------------------------------------------------------
log "running unified healthcheck"
if "${SCRIPT_DIR}/healthcheck.sh"; then
  echo "${TARGET_SHA}" > "${DEPLOYED_SHA_FILE}"
  printf '\n'
  ok "update complete"
  printf '  previous SHA: %s\n' "${PREV_SHA}"
  printf '  current  SHA: %s\n' "${TARGET_SHA}"
  printf '  result:       DEPLOYED\n'
else
  printf '\n'
  die "healthcheck FAILED after upgrade to ${TARGET_SHA}

Rollback (CODE only — does not touch the database):
  cd %s
  git checkout %s
  ./infra/deploy/deploy.sh

Database recovery, if needed, must use the approved backup/restore runbook on an
ISOLATED target first (infra/deploy/restore-drill.sh). Do NOT assume
'alembic downgrade' is safe — schema/data rollback is not automated." \
    "${PROJECT_ROOT}" "${PREV_SHA}"
fi
