# Production deployment (T035)

Single internal-server Docker Compose topology (design §§4,7,9; ADR 0003/0006/0030).
The stack runs PostgreSQL 16, Redis 7 (broker only), the FastAPI API, a Celery
worker, the ADR 0030 single-active scheduler process, the Vue frontend and a
TLS reverse proxy. The production runtime contains **no NestJS / Prisma** — the
legacy stack is reference-only.

Single-server deployment:
docs/deployment/SINGLE-SERVER-DOCKER-COMPOSE.md (tutorial) and `infra/deploy/`
(operator scripts: deploy / update / healthcheck / status / logs / start /
stop / restart / backup / restore-drill).

## Services

| Service    | Image / build                | Role |
|------------|------------------------------|------|
| `postgres` | `postgres:16-alpine`         | Sole database; persistent volume; no host port published (opt-in via `docker-compose.host-postgres.yml` override for local dev) |
| `redis`    | `redis:7-alpine`             | Celery broker only, ephemeral (not a fact source; ADR 0006) |
| `api`      | `risk-platform-api` (built)  | FastAPI modular monolith; `uvicorn risk_platform.main:app` |
| `worker`   | `risk-platform-api` (reused) | Celery worker (`risk_platform.worker`); shares image + storage volume |
| `scheduler`| `risk-platform-api` (reused) | ADR 0030 single-active scheduler (`risk_platform.scheduler:main`, invoked explicitly — see note); liveness `:9191` |
| `web`      | `risk-platform-web` (built)  | Vite-built Vue SPA served by nginx |
| `proxy`    | `nginx:1.27-alpine`          | TLS termination, security headers, routing (`/api` → api, `/` → web, SSE no-buffer) |

The API, worker and scheduler share one production image (`risk-platform-api`);
only the per-service `command` differs. The scheduler uses the T046 entrypoint
unchanged — no scheduler logic lives in T035.

## Request origin validation

`REQUEST_ORIGIN_VALIDATION_ENABLED=true` is the production default. It protects
mutating requests by rejecting an untrusted supplied Origin at the API boundary
(the current implementation has no separate Referer check). Set it to `false` only in a controlled test or troubleshooting
environment; this narrowly skips that request-origin check and does not disable
authentication, session/cookie validation, RBAC, CORS, or other security controls.
Production deployments must keep it set to `true`.

## AI Provider internal endpoint allowlist

Public HTTPS AI APIs work with the default empty allowlists. An internal
Provider requires both its exact hostname and every permitted DNS CIDR:

```dotenv
AI_OUTBOUND_ALLOWED_HOSTNAMES=token.longshine.com
AI_OUTBOUND_ALLOWED_CIDRS=10.0.0.0/8
```

This permits `https://token.longshine.com:18443` only when DNS resolves in
`10.0.0.0/8`. Localhost, link-local, metadata and other forbidden addresses
remain blocked even if listed. API, worker and scheduler receive the same
values; only AI calls consume them, so IMAP is not widened.

## WeChat mini-program SSO

WeChat SSO is optional and is consumed only by the API container. Set the
environment-specific `WECHAT_USER_INFO_URL` in the gitignored `.env.production`
file; do not hard-code a test or production endpoint in the repository. The
timeout and retry defaults are `5` seconds and `2` retries. The worker and
scheduler do not receive these settings because they do not construct the
WeChat authentication client.

## Networking and trust

- `project-risk-backend` (fixed subnet `10.30.0.0/24`) is the internal app
  network. The proxy is the **only** service exposed to the host (`:8443`).
- `TRUSTED_PROXY_CIDRS=10.30.0.0/24` is set on the API so its
  `TrustedProxyHeadersMiddleware` honors `X-Forwarded-For` / `X-Forwarded-Proto`
  from the proxy (and only from the proxy).
- `project-risk-network` (existing, unpinned) is kept for `pnpm db:up` and
  standalone integration-test containers; `postgres` is attached to both.
- `postgres` publishes **no host port** — the stack reaches it over the compose
  network (`DATABASE_URL` → `postgres:5432`), so a host process using
  `127.0.0.1:5432` cannot block a deployment. Local dev / integration tests opt
  into a `127.0.0.1` bind by including `infra/docker-compose.host-postgres.yml`
  in `COMPOSE_FILE` (as `pnpm db:up` does) and setting `POSTGRES_HOST_PORT` —
  independent of the fixed in-container 5432.

## Secrets

No credential is committed. Secrets are injected via:

- **`DATA_ENCRYPTION_KEY`**, **`POSTGRES_PASSWORD`**, **`CORS_ORIGIN`**,
  **`INITIAL_ADMIN_PASSWORD`**: env vars from a gitignored `.env.production`.
- **Session signing key**: a compose `secrets` read-only file mounted at
  `/run/secrets/project_risk_session_key` (the app reads `SESSION_SECRET_FILE`).
- **TLS certificate/key**: bind-mounted read-only from `infra/proxy/certs/`
  (gitignored; self-signed for test, CA-issued for real deployment).

Generate test secrets + cert:

```bash
bash infra/scripts/init-secrets.sh
```

## First-time init

The application never creates schema at startup (ADR 0010). Use the deploy kit's
`--seed` flow, which runs the migration **and** the initial-admin seed through
one-shot api containers — it does not require a long-running api container:

```bash
./infra/deploy/deploy.sh --seed
```

The equivalent raw commands (migration + seed each run in a throwaway
`compose run` container; the seed receives `INITIAL_ADMIN_PASSWORD` explicitly,
never echoed):

```bash
docker compose --env-file .env.production -f infra/docker-compose.yml run --rm --no-deps api alembic upgrade head
docker compose --env-file .env.production -f infra/docker-compose.yml run --rm --no-deps \
  -e INITIAL_ADMIN_PASSWORD="$INITIAL_ADMIN_PASSWORD" api risk-platform-seed
```

## Operate

```bash
# Build images and start the whole stack
docker compose --env-file .env.production -f infra/docker-compose.yml up -d --build

# Tail logs
docker compose --env-file .env.production -f infra/docker-compose.yml logs -f

# Health
docker compose --env-file .env.production -f infra/docker-compose.yml ps

# Local dev/integration (postgres only, unchanged from before)
pnpm db:up
```

Access the app at `https://<host>:${PROXY_HTTPS_PORT:-8443}` (self-signed cert →
browser warning in test). API health: `GET /api/health`. Scheduler liveness is
internal (`:9191` within the scheduler container).

## Agent SSE

The reverse proxy matches `^/api/agent/conversations/.+/events$` with
`proxy_buffering off`, `proxy_cache off`, long read/send timeouts and chunked
transfer — buffering/timeout must not break the SSE stream (ADR 0016).

## Single-active scheduler (ADR 0030)

The scheduler service runs the T046 entrypoint `risk_platform.scheduler:main`.
Because `scheduler.py` has no `if __name__ == "__main__"` guard, the Compose
`command` calls `main()` explicitly (`python -c "from
risk_platform.scheduler import main; main()"`) rather than `python -m
risk_platform.scheduler` (which would import-and-exit 0); this invokes the
existing entrypoint unchanged and adds no scheduler logic.

Only one scheduler can hold the PostgreSQL advisory lock. A second instance
fail-fast exits non-zero and `restart: unless-stopped` retries until it can take
over. Cadence env (`SCHEDULER_*`) are operational defaults, not SLOs (DG-05 out
of scope). The scheduler writes no business audit (ADR 0017) and is the **only**
publisher of the transactional outbox — the request path writes PostgreSQL alone
(no DB/Celery dual-write).

## Out of scope (T035)

- **Backup/restore** is owned by T036 and blocked on DG-08 (backup encryption /
  consistency). T035 only persists the PostgreSQL and application/import volumes;
  no backup logic or `infra/backup/**` is introduced.
- **DG-05** numeric performance thresholds remain out of scope.
- Real CA certificates and production-grade secret management are operator
  responsibilities; T035 validates with self-signed test material.
