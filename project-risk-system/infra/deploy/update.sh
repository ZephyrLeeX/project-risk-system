#!/usr/bin/env bash
# Deploy a specific, pinned release onto an already-running single-server stack.
#
# Usage:
#   ./infra/deploy/update.sh <git-tag-or-commit>
#   ./infra/deploy/update.sh --help
#
# Flow: clean tree -> fetch -> resolve target -> record previous SHA ->
# (optional) pre-update backup -> checkout pinned SHA -> compose config ->
# rebuild -> migrate -> recreate stack -> healthcheck.
#
# This script NEVER automatically downgrades the database. If health fails after
# an upgrade, it reports the previous code SHA to roll back to and points at the
# approved backup/restore runbook for any database recovery.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init
deploy_conf_load

usage() {
  cat <<'EOF'
Usage: update.sh <git-tag-or-commit> [--no-backup] [--help]

  <git-tag-or-commit>   Pinned release to deploy. Production must never deploy
                        an unpinned branch HEAD — pass an explicit tag or SHA.
  --no-backup           Skip the suggested pre-update backup (NOT recommended).
  --help                Show this help.

Rollback boundary:
  Code can be rolled back by checking out the previous SHA. Database recovery
  must use the approved backup/restore runbook (infra/deploy/restore-drill.sh
  on an isolated target). Automatic Alembic downgrade is NOT performed and is
  not assumed safe.
EOF
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

# --- 6. compose config -------------------------------------------------------
log "validating compose config"
compose config --quiet || die "docker compose config failed after checkout"

# --- 7. rebuild images -------------------------------------------------------
log "rebuilding production images"
compose build --pull api web || die "image rebuild failed"

# --- 8. migrate --------------------------------------------------------------
log "applying alembic migrations (upgrade head only; no downgrade)"
# Ensure the durable layer is up (it should be on an existing deployment).
compose up -d --no-deps postgres redis >/dev/null 2>&1 || true
compose exec -T api alembic upgrade head \
  || die "alembic upgrade head failed after upgrade — DO NOT downgrade blindly; see rollback note below"

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
