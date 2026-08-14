#!/usr/bin/env bash
# Generate the non-committed production secrets and a self-signed TLS cert for
# the reverse proxy. Outputs are gitignored. Run from the repository root.
#
#   bash infra/scripts/init-secrets.sh
#
# This generates TEST-grade material sufficient to boot and validate the stack
# (T035 acceptance: "test secrets/cert"). For a real deployment replace the TLS
# cert with a CA-issued certificate and rotate all generated values.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRETS_DIR="${ROOT}/infra/secrets"
CERTS_DIR="${ROOT}/infra/proxy/certs"

mkdir -p "${SECRETS_DIR}" "${CERTS_DIR}"
chmod 700 "${SECRETS_DIR}" "${CERTS_DIR}"

# --- Session signing key (compose secret, read-only file) ---
SESSION_KEY_FILE="${SECRETS_DIR}/project_risk_session_key"
if [ -f "${SESSION_KEY_FILE}" ]; then
  echo "session key exists, leaving unchanged: ${SESSION_KEY_FILE}"
else
  # >= 48 bytes, well above the app's minimum session key length.
  openssl rand -base64 48 > "${SESSION_KEY_FILE}"
  # 0644: the API runs as non-root appuser (uid 1001) and compose file-secrets
  # are bind-mounted with the host file's ownership, so the key must be
  # readable by that uid. On a hardened multi-tenant host, chown the file to
  # the container uid (1001) and tighten to 0640 instead.
  chmod 0644 "${SESSION_KEY_FILE}"
  echo "generated session key: ${SESSION_KEY_FILE}"
fi

# --- Self-signed TLS cert for the reverse proxy (test only) ---
if [ -f "${CERTS_DIR}/tls.crt" ] && [ -f "${CERTS_DIR}/tls.key" ]; then
  echo "TLS cert exists, leaving unchanged: ${CERTS_DIR}"
else
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "${CERTS_DIR}/tls.key" -out "${CERTS_DIR}/tls.crt" \
    -subj "/CN=risk.example.internal" >/dev/null 2>&1
  chmod 600 "${CERTS_DIR}/tls.key"
  echo "generated self-signed TLS cert: ${CERTS_DIR}"
fi

cat <<EOF

Secrets ready (gitignored). Now create .env.production from infra/env.example
and fill real values — at minimum:

  POSTGRES_PASSWORD   (URL-safe; or reuse the one below)
  DATA_ENCRYPTION_KEY (openssl rand -base64 32)
  CORS_ORIGIN         (e.g. https://risk.example.internal:8443)
  INITIAL_ADMIN_PASSWORD

A URL-safe Postgres password you can use:
  $(openssl rand -base64 24 | tr '+/' '-_' | tr -d '=')

Then:
  docker compose --env-file .env.production -f infra/docker-compose.yml up -d --build
EOF
