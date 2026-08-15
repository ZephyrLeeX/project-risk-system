# Operator Handoff — INTERNAL_MVP

- **Release profile:** `INTERNAL_MVP`（ADR 0033）—— 低并发、内部使用
- **代码基线（HEAD）:** `60f59fb2c88fedaab7fcb014494563ca019796f9`
- **部署形态:** 单台内网服务器 Docker Compose（7 服务）

> 面向运维操作员。命令、路径、环境变量、标识符保持原样。所有真实 secret 由操作员生成，
> **不提交**。

## 1. 前置条件

- Docker + Docker Compose（compose v2）。
- 单台内网服务器，可访问内部网络；**不暴露公网**（INTERNAL_MVP 不作为公网 / 高并发 /
  外部 Provider 发布）。
- 操作员自备：CA 签发 TLS 证书（或测试用自签名）、真实 `DATA_ENCRYPTION_KEY`、
  `POSTGRES_PASSWORD`、`CORS_ORIGIN`、`INITIAL_ADMIN_PASSWORD`、备份 KEK。
- 仓库根: `/home/lijx/workspace/project-risk-system`；应用与部署配置在嵌套的
  `project-risk-system/` 子目录（下文路径均相对仓库根）。

## 2. Secrets 初始化（首次）

无任何 credential 提交。生成测试级 secret + 自签名 TLS 证书（gitignored）：

```bash
cd project-risk-system
bash infra/scripts/init-secrets.sh
```

该脚本生成（均为 gitignored，仅本机磁盘）：
- `infra/secrets/project_risk_session_key`（compose secret，read-only 挂载到
  `/run/secrets/project_risk_session_key`，≥48 字节）。
- `infra/proxy/certs/tls.crt` + `tls.key`（自签名，CN=risk.example.internal，测试用）。

> 真实部署须替换为 CA 签发证书并轮换所有生成值。备份 KEK 独立于
> `DATA_ENCRYPTION_KEY`，由操作员在宿主机生成（见第 6 节）。

## 3. 环境配置

从模板创建 `.env.production`（gitignored），填入真实值：

```bash
cp infra/env.example .env.production
# 编辑 .env.production，至少填:
#   POSTGRES_PASSWORD        (URL-safe; openssl rand -base64 24 | tr '+/' '-_' | tr -d '=')
#   DATA_ENCRYPTION_KEY      (openssl rand -base64 32)
#   CORS_ORIGIN              (e.g. https://risk.example.internal:8443)
#   INITIAL_ADMIN_PASSWORD
```

`.env.production` 所有未填项为占位符（`replace_with_*`），**不得直接用于部署**。

## 4. 首次部署与数据库初始化

应用**从不在启动时创建 schema**（ADR 0010）。初始化步骤明确：

```bash
# 1. 构建镜像并启动全栈（postgres 先 healthy，再 api/worker/scheduler/web/proxy）
docker compose --env-file .env.production -f infra/docker-compose.yml up -d --build

# 2. 数据库 migration（首次，alembic head）
docker compose --env-file .env.production -f infra/docker-compose.yml exec api alembic upgrade head

# 3. seed + 初始 admin bootstrap（使用 INITIAL_ADMIN_PASSWORD）
docker compose --env-file .env.production -f infra/docker-compose.yml exec api risk-platform-seed
```

seed 幂等，创建四角色 / 权限 / 参考数据 + 初始管理员（`INITIAL_ADMIN_USERNAME` /
`INITIAL_ADMIN_PASSWORD`）。重复执行不重复创建 demo。

## 5. Health checks

| 服务 | healthcheck | 方式 |
|---|---|---|
| PostgreSQL | ✓ | `pg_isready -U <user> -d <db>`，5s interval |
| Redis | ✓ | `redis-cli ping \| grep -q PONG`，5s interval |
| API | ✓ | `GET /api/health`（容器内 `http://127.0.0.1:3000/api/health`），10s interval |
| worker | ✓ | `celery inspect ping -d celery@worker1`，30s interval |
| scheduler | ✓ | ADR 0030 liveness `:9191`（advisory lock 持有 + 最近 tick 在窗口内），10s interval |
| proxy | ✗ 无独立 healthcheck | 依赖 `api` service_healthy + `web` service_started |
| web | ✗ 无独立 healthcheck | 静态 SPA，service_started |

- 查看整体健康: `docker compose --env-file .env.production -f infra/docker-compose.yml ps`
- API 健康端点（经 proxy）: `GET https://<host>:8443/api/health`
- scheduler liveness 仅容器内部（`:9191`，不对外暴露）。

> **非阻断观察:** proxy / web 无独立 healthcheck。proxy 依赖 api healthy 才启动；
> web 仅服务静态资源。如需更细粒度探活，可在后续 hardening 为 nginx 加
> `wget/curl /` healthcheck —— 不属于 INTERNAL_MVP release blocker。

## 6. 备份与恢复（one-shot runbook）

完整 runbook: `project-risk-system/infra/backup/README.md`。要点：

- 备份 authority = PostgreSQL（`pg_dump -Fc` 单逻辑快照）+ durable file storage
  （`project-risk-storage` volume tar）；Redis/Celery/cache/temp/log 排除。
- quiesce-coordinated：备份前 `stop api worker scheduler`（postgres+redis 保持运行）。
- AES-256-GCM envelope：per-backup DEK 由 versioned backup KEK 包装（KEK 独立于
  `DATA_ENCRYPTION_KEY`，从宿主机 read-only 文件加载，**从不入 env / 不提交**）。
- restore 为 fail-closed isolated target（仅空 DB + 空 storage；partial-set abort）。

生成备份 KEK（宿主机，gitignored）：

```sh
openssl rand -base64 32 > /etc/risk/backup-keys/backup_kek_v1
chmod 0400 /etc/risk/backup-keys/backup_kek_v1
```

备份（one-shot，api 镜像运行 orchestrator，`pg_dump` 在 postgres 容器内）：

```sh
docker compose --env-file .env.production -f infra/docker-compose.yml stop api worker scheduler
docker compose --env-file .env.production -f infra/docker-compose.yml run --rm --no-deps \
  -v project-risk-storage:/app/storage:ro -v /var/backups/risk:/backup \
  -v /etc/risk/backup-keys:/keys:ro -v "$(pwd)/infra/backup/src:/opt/risk_backup:ro" \
  -e PYTHONPATH=/opt/risk_backup \
  api python -m risk_backup backup --type daily \
    --dsn "postgresql://project_risk:${POSTGRES_PASSWORD}@postgres:5432/project_risk" \
    --pg-runner "docker exec -i project-risk-postgres" --pg-socket-dir /var/run/postgresql \
    --pg-user project_risk --pg-db project_risk --storage-root /app/storage \
    --output /backup/daily.rpbk --temp-dir /tmp/risk-backup \
    --kek-version v1 --kek-file v1=/keys/backup_kek_v1 --quiesce none
docker compose --env-file .env.production -f infra/docker-compose.yml up -d --no-deps api worker scheduler
```

恢复 drill（隔离空目标，ADR 0009 — 备份仅在真实恢复后有效）：见
`infra/backup/README.md`「Isolated restore drill」段。

## 7. Rollback / restore 流程

INTERNAL_MVP 的回滚 = 从最近有效加密备份恢复到隔离目标后切换（**不直接覆盖 live
system**）：

1. 确认最近一次 `daily.rpbk`（含完整 audit hash-chain + alembic head 校验通过）。
2. 按第 6 节 / `infra/backup/README.md` 在隔离空 DB + 空 storage 执行 restore，验证
   audit hash-chain、alembic-head match、file reconcile 全部 PASS。
3. 验证通过后切换流量到恢复目标（cutover 由操作员执行；INTERNAL_MVP 单机可停服切换）。
4. 若 restore 任一步 fail-closed（key 不匹配 / tamper / partial-set / audit 链断裂），
   **不得**使用该备份，回退到更早的有效备份。

> 前端 / 镜像回滚：`risk-platform-api:0.1.0` / `risk-platform-web:0.1.0` 为本发布镜像；
   代码回滚 = `git checkout 60f59fb` + rebuild。

## 8. Operator contact procedure

- INTERNAL_MVP 为内部发布；**操作员联系渠道由部署组织自定义**，不写入仓库（无真实
  联系人 / 邮箱 / 电话提交）。
- 建议操作员在内部记录：部署负责人、备份 KEK 保管人、恢复演练窗口、on-call 联系方式。
- 事故上报路径：按内部安全 / 审计流程；审计哈希链（ADR 0008）提供事后可核查轨迹。

## 9. 日常操作

```bash
# 启动
docker compose --env-file .env.production -f infra/docker-compose.yml up -d --build
# 日志
docker compose --env-file .env.production -f infra/docker-compose.yml logs -f
# 健康
docker compose --env-file .env.production -f infra/docker-compose.yml ps
# 停止
docker compose --env-file .env.production -f infra/docker-compose.yml down
```

访问: `https://<host>:${PROXY_HTTPS_PORT:-8443}`（自签名证书 → 浏览器警告）。

## 10. 升级到 PRODUCTION_CAPACITY_READY 前不得执行

- 不得把本首版暴露到公网 / 高并发 / 外部 Provider。
- 不得声称 capacity-certified。
- 不得在未重跑 T038（连续 2 次 hard gate PASS）前执行 T039（真实外部 E2E / cutover）。
- 升级路径详见 `INTERNAL-MVP-RELEASE-NOTES.md` 第 4 节与 ADR 0033 §2。
