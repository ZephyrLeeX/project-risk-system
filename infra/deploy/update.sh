#!/usr/bin/env bash
# Deploy a pinned release onto the single-server Docker Compose stack.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

usage() {
  cat <<'USAGE'
Usage: update.sh <git-tag-or-commit> [--no-backup] [--help]

  <git-tag-or-commit>   Pinned release to deploy; branch HEADs are not accepted
                        as an operational convention.
  --no-backup           Skip the suggested pre-update backup (not recommended).
  --help                Show this help.
USAGE
}

cd "${PROJECT_ROOT}"
require_cmd git
require_cmd docker "Docker Engine"
require_project_root
require_env_file

RESUMED_FROM_BRIDGE=0
NO_BACKUP=0
TARGET_SHA=""
PREV_SHA=""

if [[ -n "${RISK_UPDATE_BRIDGE_TARGET_SHA:-}" ]]; then
  RESUMED_FROM_BRIDGE=1
  TARGET_SHA="${RISK_UPDATE_BRIDGE_TARGET_SHA}"
  CURRENT_SHA="$(git rev-parse HEAD)"
  [[ "${CURRENT_SHA}" == "${TARGET_SHA}" ]] \
    || die "bridge resume target mismatch: expected ${TARGET_SHA}, current ${CURRENT_SHA}"
  PREV_SHA="${RISK_UPDATE_BRIDGE_PREV_SHA:-}"
  [[ -n "${PREV_SHA}" ]] || die "bridge resume is missing previous deployed SHA"
  log "resuming repository-layout migration at ${TARGET_SHA}"
else
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

  log "checking working tree is clean"
  if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    die "working tree not clean; commit or stash before updating"
  fi

  log "fetching origin"
  git fetch --tags origin || die "git fetch failed (network/remote?)"
  TARGET_SHA="$(git rev-parse --verify "${TARGET}^{commit}" 2>/dev/null || true)"
  [[ -n "${TARGET_SHA}" ]] || die "target does not resolve to a commit: ${TARGET}"
  log "target resolved to: ${TARGET_SHA} ($(git log -1 --format='%s' "${TARGET_SHA}"))"

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

  if (( NO_BACKUP )); then
    warn "skipping pre-update backup (--no-backup)"
  else
    log "suggested pre-update backup (recommended)"
    "${SCRIPT_DIR}/backup.sh" \
      || die "pre-update backup FAILED; aborting update (use --no-backup only deliberately)"
  fi

  log "checking out pinned release: ${TARGET_SHA}"
  git checkout "${TARGET_SHA}" || die "git checkout failed for ${TARGET_SHA}"
fi

DEPLOYED_SHA_FILE="${PROJECT_ROOT}/infra/deploy/.deployed-sha"

log "validating compose config"
compose config --quiet || die "docker compose config failed after checkout"

log "rebuilding production images"
compose build --pull api web || die "image rebuild failed"

log "applying alembic migrations (upgrade head only; no downgrade)"
compose up -d --no-deps postgres redis >/dev/null 2>&1 || true
compose_run_api alembic upgrade head \
  || die "alembic upgrade head failed after upgrade — do not downgrade blindly"
verify_migration_head

log "recreating stack"
compose up -d || die "failed to recreate stack"

log "running unified healthcheck"
if "${SCRIPT_DIR}/healthcheck.sh"; then
  echo "${TARGET_SHA}" > "${DEPLOYED_SHA_FILE}"
  printf '\n'
  ok "update complete"
  printf '  previous SHA: %s\n' "${PREV_SHA}"
  printf '  current  SHA: %s\n' "${TARGET_SHA}"
  printf '  result:       DEPLOYED\n'
  (( RESUMED_FROM_BRIDGE )) && ok "repository-layout migration completed"
else
  printf '\n'
  die "healthcheck FAILED after upgrade to ${TARGET_SHA}

Rollback is code-only and must use the previous layout that belongs to ${PREV_SHA}:
  git checkout ${PREV_SHA}
  if [ -x infra/deploy/deploy.sh ]; then ./infra/deploy/deploy.sh; else ./project-risk-system/infra/deploy/deploy.sh; fi

Database recovery, if needed, must use the approved isolated restore drill."
fi
