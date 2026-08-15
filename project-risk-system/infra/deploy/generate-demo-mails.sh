#!/usr/bin/env bash
# Generate synthetic INTERNAL_MVP demo mail fixtures (no SMTP, no send).
#
# Produces markdown + RFC 5322 .eml + manifest.json + README.md + 4 binary
# attachment fixtures (.txt/.pdf/.docx/.xlsx) under artifacts/demo-mails/
# (gitignored). Every subject is [WSLDEMO]-prefixed and every body carries a
# synthetic-data banner. Project names align to the demo business-data seed
# so mailbox project matching resolves against real, existing projects.
#
# This script does NOT send mail, implement SMTP, touch mailbox ingest, or
# write anything to the database. The user copies Subject/Body by hand to a
# real test mailbox; the system then reads it through the normal
# IMAP -> scheduler -> worker -> parser -> Provider path.
#
# Usage:
#   ./infra/deploy/generate-demo-mails.sh            # generate
#   ./infra/deploy/generate-demo-mails.sh --validate # validate existing
#   ./infra/deploy/generate-demo-mails.sh --help
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
deploy_root_init

GENERATOR="${SCRIPT_DIR}/lib/generate_demo_mails.py"
OUT_DIR_NAME="artifacts/demo-mails"

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { cat <<'EOF'
Usage: generate-demo-mails.sh [--validate] [--help]

Generates synthetic demo mail fixtures into artifacts/demo-mails/ (gitignored).
With --validate, only runs the validation pass over an existing output dir.

Boundary: this tool never sends mail, never implements SMTP, never touches
mailbox ingest or the database. You copy Subject/Body by hand to a real test
mailbox; the system reads it via the normal IMAP -> scheduler -> worker ->
parser -> Provider path.
EOF
exit 0; }

cd "${PROJECT_ROOT}"

require_cmd python3 "Python 3"

# Prefer the api-python venv interpreter when present: it has pypdf + the
# project's own risk_platform.mailbox.parsing, so validate can run the real
# production parse_attachment/parse_mail checks. Fall back to system python3
# (stdlib-only checks still run; deep parse is skipped with a notice).
PYTHON_BIN="python3"
VENV_PY="${PROJECT_ROOT}/apps/api-python/.venv/bin/python"
if [[ -x "${VENV_PY}" ]]; then
  PYTHON_BIN="${VENV_PY}"
fi

COMMAND="generate"
if [[ "${1:-}" == "--validate" ]]; then
  COMMAND="validate"
fi

export PROJECT_ROOT

if [[ "${COMMAND}" == "generate" ]]; then
  rm -rf "${PROJECT_ROOT}/${OUT_DIR_NAME}"
  log "generating demo mail fixtures -> ${OUT_DIR_NAME}/"
  "${PYTHON_BIN}" "${GENERATOR}" generate
else
  log "validating demo mail fixtures in ${OUT_DIR_NAME}/"
fi
"${PYTHON_BIN}" "${GENERATOR}" "${COMMAND}"

ok "demo mail fixtures: ${OUT_DIR_NAME}/"
if [[ "${COMMAND}" == "generate" ]]; then
  printf '%s\n' "  - open a .md file, copy its Subject + Body,"
  printf '%s\n' "  - send to the test mailbox configured into the system,"
  printf '%s\n' "  - wait for scheduler/mailbox sync (or trigger sync),"
  printf '%s\n' "  - inspect Mail Sync Summary / Messages / Candidate /"
  printf '%s\n' "    AI classification / Project mapping / Risk / Timeline / Audit."
fi
