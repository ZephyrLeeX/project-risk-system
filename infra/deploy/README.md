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
| `seed-demo-data.sh --confirm-demo-data` | Load repeatable synthetic WSLDEMO users/projects/risks/todos through the Compose API image. Never deletes data or creates mail accounts. |
| `deploy.conf.example` | Non-secret config template. Copy to `deploy.conf` (gitignored). |
| `lib/common.sh` | Shared helpers (sourced by every script). |
| `lib/generate_demo_mails.py` | Pure-stdlib fixture generator (called by `generate-demo-mails.sh`). |

## Quick start

```bash
cp infra/env.example .env.production        # fill real values
bash infra/scripts/init-secrets.sh          # session key + TLS cert
./infra/deploy/deploy.sh --seed             # first-time deploy + admin bootstrap
./infra/deploy/seed-demo-data.sh --confirm-demo-data
./infra/deploy/generate-demo-mails.sh
./infra/deploy/generate-demo-mails.sh --validate
./infra/deploy/healthcheck.sh
```

推荐演示顺序是：先完成 migration 和 initial admin bootstrap，再执行上述
`seed-demo-data.sh`。之后把 `artifacts/demo-mails/*.md` 的 Subject/Body 内容
手工发送到真实测试邮箱（脚本不会发信、不会创建邮箱），验证链路：

`IMAP -> scheduler -> worker -> parser -> Provider -> candidate/review -> Risk/Todo`。

Demo seed 只接受显式的 `--confirm-demo-data`，使用稳定的 `WSLDEMO` key
重复执行不会重复创建数据，也不会覆盖 initial admin、删除现有业务数据或 reset
database。所有用户、项目、风险和待办均为 synthetic；正式风险状态仍遵守模型的
`ACTIVE` / `RESOLVED` 约束，展示阶段写在合成风险标题和说明中。

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
