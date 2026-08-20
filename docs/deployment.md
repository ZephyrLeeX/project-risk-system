# 部署与运维

## 生产拓扑

生产环境是单台内网服务器上的 Docker Compose：PostgreSQL 16、Redis 7、FastAPI API、Celery worker、scheduler、Vue/nginx web 和 TLS reverse proxy。只有 proxy 暴露主机端口。

正式 Compose 文件：`infra/docker-compose.yml`。API image 由 `apps/api` 构建，worker 与 scheduler 复用同一 image。服务名、container 名和 named volume 属于升级兼容边界。

持久 volume：

- `project-risk-postgres-data`
- `project-risk-storage`

目录重构不会重建或改名这两个 volume。

## 首次部署

1. 从 `infra/env.example` 创建 `.env.production` 并填写真实值。
2. 运行 `bash infra/scripts/init-secrets.sh`，生产环境应替换测试证书并按组织规则管理密钥。
3. 从 `infra/deploy/deploy.conf.example` 创建 gitignored 的 `infra/deploy/deploy.conf`，按服务器环境调整非 secret 参数。
4. 使用 `infra/deploy/deploy.sh` 或等价受控流程部署。

常用 Compose 验证：

```sh
docker compose --env-file .env.production -f infra/docker-compose.yml config
docker compose --env-file .env.production -f infra/docker-compose.yml ps
```

## 升级

`infra/deploy/update.sh <tag-or-sha>` 只接受可解析的固定 Git target，升级前建议执行加密备份，随后构建 API/web image、执行 `alembic upgrade head`、重建 stack 并运行统一 healthcheck。

从历史 `project-risk-system/` 目录布局升级到当前仓库根布局时，必须先部署 bridge commit，再使用 bridge 版 `update.sh` 升到新布局。bridge 会复制而不覆盖 `.env.production`、`deploy.conf`、`.deployed-sha`、Compose secret 和 TLS cert；旧副本保留供代码回滚使用。

## 健康检查

`infra/deploy/healthcheck.sh` 检查 Compose service、PostgreSQL、Redis、FastAPI `/api/health`、TLS proxy、web、Celery worker、scheduler single-active lock 以及 required volume mounts。只有所有探针通过才记录新 deployed SHA。

## 备份与恢复

`infra/deploy/backup.sh` 调用批准的加密备份实现；KEK 从主机只读文件加载，内容不得写入仓库或日志。备份只有在 isolated restore drill 成功后才可视为可信。

恢复演练使用 `infra/deploy/restore-drill.sh`，必须指定隔离数据库和隔离 storage，脚本会拒绝生产数据库目标。生产 schema/data rollback 不由 `alembic downgrade` 自动完成。

## 安全

`.env.production`、`infra/secrets/`、`infra/proxy/certs/`、`infra/deploy/deploy.conf`、`.deployed-sha` 都必须保持 Git 忽略。生产凭据不可写入 issue、日志或提交记录。
