# Single-server Docker Compose deployment kit (INTERNAL_MVP)

Operator-facing scripts that wrap the validated T035 compose stack, the T046
scheduler, the T036 backup/restore CLI, and the existing migration/seed
commands. **No second deployment architecture is introduced** — every script
calls `infra/docker-compose.yml` and the approved commands.

Full walkthrough: [`docs/deployment/SINGLE-SERVER-DOCKER-COMPOSE.md`](../../docs/deployment/SINGLE-SERVER-DOCKER-COMPOSE.md).

## Files

| Script | Purpose |
|--------|---------|
| `deploy.sh` | First-time deploy (build → migrate via one-shot api container → verify at head → optional seed → up → health). `--seed` for initial admin. |
| `update.sh <tag\|sha>` | Upgrade to a pinned release (clean tree, fetch, backup, checkout, rebuild, migrate, recreate, health). |
| `healthcheck.sh` | Unified health check; prints `HEALTHCHECK_OK` or exits non-zero. |
| `status.sh` | Deployed SHA, compose ps, images, health summary, volumes, recent logs. No secrets printed. |
| `logs.sh [service]` | `docker compose logs -f` wrapper. |
| `start.sh` / `stop.sh` / `restart.sh` | Safe lifecycle wrappers; never remove volumes. |
| `backup.sh` | Thin wrapper over the T036 backup CLI; reports backupId/status/artifact. |
| `restore-drill.sh` | Isolated restore drill; fail-closed against the production DB. |
| `generate-demo-mails.sh` | Generate synthetic demo mail fixtures (`artifacts/demo-mails/`, gitignored). No send, no SMTP, no DB writes. |
| `deploy.conf.example` | Non-secret config template. Copy to `deploy.conf` (gitignored). |
| `lib/common.sh` | Shared helpers (sourced by every script). |
| `lib/generate_demo_mails.py` | Pure-stdlib fixture generator (called by `generate-demo-mails.sh`). |

## Quick start

```bash
cp infra/env.example .env.production        # fill real values
bash infra/scripts/init-secrets.sh          # session key + TLS cert
./infra/deploy/deploy.sh --seed             # first-time deploy + admin bootstrap
./infra/deploy/healthcheck.sh
```

## Safety properties

- Every script uses `set -Eeuo pipefail`; arguments are quoted.
- No secret is echoed or passed through `set -x`.
- Migration and seed run in one-shot `compose run --rm --no-deps api` containers:
  they never depend on a long-running api container, and their success is judged
  by the command's own exit code — an api HTTP `unhealthy` (503) state is never
  treated as a failure. The seed receives `INITIAL_ADMIN_*` explicitly (values
  read from the env file, never printed).
- `stop.sh` / `restart.sh` refuse `--volumes`; `docker compose down -v` is never used.
- `update.sh` never auto-downgrades the database.
- `restore-drill.sh` is fail-closed: it refuses any target matching the production DB.
- Backups are never deleted by these scripts.
