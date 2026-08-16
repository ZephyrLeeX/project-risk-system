# 单台服务器 Docker Compose 部署教程（INTERNAL_MVP）

本教程面向**第一次部署本项目**的运维 / 开发人员，从一台全新的 Ubuntu/Linux 内网服务器开始，完成首次部署、升级、健康检查、备份与故障排查。

本教程不引入第二套部署架构。所有命令都基于已验收的 T035 单机 Docker Compose、T046 scheduler、T036 备份/恢复实现以及现有的 migration / seed 命令。

- 发布配置：`INTERNAL_MVP`（`READY FOR DEPLOYMENT`）
- 发布内容 checkpoint：`305706f8f42a6b92223f353e387f0c92dbba643b`
- 适用范围：**低并发、内部使用**；**未通过 capacity 认证**（见第 16 节）

> 本文中所有 `./infra/...` 与 `infra/...` 路径都相对于**可部署工程根目录**（即包含 `infra/` 和 `apps/` 的目录）。请始终在该目录下执行命令。

---

## 1. 部署架构

单机内网部署包含 7 个服务，全部由 `infra/docker-compose.yml` 编排：

| 服务 | 镜像 / 构建 | 角色 |
|------|-------------|------|
| `postgres` | `postgres:16-alpine` | 唯一数据库，持久卷；仅 127.0.0.1 绑定（开发/测试） |
| `redis` | `redis:7-alpine` | 仅作 Celery broker，内存、非持久、不是事实源 |
| `api` | `risk-platform-api`（构建） | FastAPI 单体；`uvicorn risk_platform.main:app`，监听 3000 |
| `worker` | `risk-platform-api`（复用） | Celery worker（`risk_platform.worker`），共享 storage 卷 |
| `scheduler` | `risk-platform-api`（复用） | ADR 0030 单活调度器，liveness `:9191` |
| `web` | `risk-platform-web`（构建） | Vite 构建的 Vue SPA，由 nginx 提供静态文件 |
| `proxy` | `nginx:1.27-alpine` | TLS 终止、安全头、路由（`/api` → api、`/` → web、SSE 不缓冲） |

关键设计约束：

- **PostgreSQL 是唯一持久事实源**（durable authority）。所有业务数据、审计链、事务性 outbox 都落 PostgreSQL。
- **Redis 只是 broker**，不持久化、不是事实源；broker 丢失由 scheduler reconciler 恢复（ADR 0006/0018/0030）。
- **scheduler 单活**：通过 PostgreSQL advisory lock 保证全局只有一个 scheduler 在运行；第二个实例会 fail-fast 退出并由 `restart: unless-stopped` 重试接管。
- **请求路径只写 PostgreSQL**，不进行 DB/Celery 双写；outbox 由 scheduler 单一负责派发到 Celery。
- 适用于 **INTERNAL_MVP 低并发内网使用**，**当前未通过 capacity 认证**（NOT capacity-certified）。
- proxy 是唯一对外暴露的服务（`${PROXY_HTTPS_PORT:-8443}:443`）；其余服务只在内部 `project-risk-backend` 网络（固定子网 `10.30.0.0/24`）。

---

## 2. 服务器准备

推荐基础要求（仅供参考，**不是**新增的 ADR 0032 硬门槛）：

- Linux/Ubuntu 22.04 或 24.04 LTS，x86_64。
- Docker Engine（官方仓库安装，见下）。
- Docker Compose v2 插件（`docker compose` 子命令）。
- Git。
- curl（健康检查脚本用于探测 proxy）。
- 2 核 / 4GB 内存 / 40GB 磁盘起步（低并发内网；按实际数据量调整）。

### 安装 Docker（官方方式）

请以 [Docker 官方文档](https://docs.docker.com/engine/install/) 为准。下面是 Ubuntu 官方仓库安装的**标准方式摘要**（如与官方文档不一致，以官方文档为准）：

```bash
# 1. 安装必要依赖
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

# 2. 添加 Docker 官方 GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 3. 添加仓库
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 4. 安装
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 5. 将当前用户加入 docker 组（重新登录生效），或使用 sudo 调用 docker
sudo usermod -aG docker "$USER"
```

验证：

```bash
docker --version
docker compose version
git --version
curl --version
```

### 目录规划

建议将工程放在固定目录，例如 `/opt/project-risk-system`：

```bash
sudo mkdir -p /opt
sudo chown "$USER":"$USER" /opt
```

### 防火墙建议

- 仅放开 proxy 的 HTTPS 端口（默认 `8443`）给内网。
- PostgreSQL（`5432`）仅绑定 `127.0.0.1`，**不要**对外暴露。
- Redis 无主机端口映射，仅容器内部可达。
- SSH 按你既有安全策略保留。

> 这些脚本不会修改宿主防火墙 / SSH 配置。防火墙规则由 operator 自行配置。

### 内网 DNS

建议为该服务器配置内网域名（例如 `risk.example.internal`），与 `.env.production` 中的 `CORS_ORIGIN` 和 TLS 证书 CN 一致。

---

## 3. 获取代码

```bash
cd /opt
git clone <仓库地址> project-risk-system
cd project-risk-system
```

**生产部署必须使用明确的 tag 或 commit SHA，不要直接部署未固定的 `main`。**

INTERNAL_MVP 发布内容 checkpoint：

```bash
git checkout 305706f8f42a6b92223f353e387f0c92dbba643b
```

> 本工程的代码在仓库的 `project-risk-system/` 子目录下（包含 `infra/`、`apps/`）。后续所有命令都在该子目录中执行：

```bash
cd project-risk-system   # 进入可部署工程根目录（含 infra/、apps/）
```

确认环境：

```bash
ls infra/docker-compose.yml infra/env.example infra/scripts/init-secrets.sh
```

---

## 4. 配置 `.env.production`

从模板复制并填写真实值（**不要**直接编辑 `infra/env.example`）：

```bash
cp infra/env.example .env.production
chmod 600 .env.production
```

逐项说明（以 `infra/env.example` 为准）：

| 变量 | 是否必填 | 说明 |
|------|----------|------|
| `CORS_ORIGIN` | 必填 | 浏览器访问的完整 origin，含协议与端口，如 `https://risk.example.internal:8443` |
| `PROXY_HTTPS_PORT` | 可选 | proxy 对外 HTTPS 端口，默认 `8443` |
| `POSTGRES_DB` | 可选 | 数据库名，默认 `project_risk` |
| `POSTGRES_USER` | 可选 | 数据库用户，默认 `project_risk` |
| `POSTGRES_PASSWORD` | **必填** | URL 安全的强密码。可用 `openssl rand -base64 24 \| tr '+/' '-_' \| tr -d '='` 生成 |
| `DATA_ENCRYPTION_KEY` | **必填** | 32 字节 base64 字段加密密钥。用 `openssl rand -base64 32` 生成 |
| `INITIAL_ADMIN_USERNAME` | 仅 seed 用 | 初始管理员用户名，默认 `admin` |
| `INITIAL_ADMIN_DISPLAY_NAME` | 仅 seed 用 | 初始管理员显示名，默认 `系统管理员` |
| `INITIAL_ADMIN_PASSWORD` | 仅 seed 用（首次必填） | 初始管理员密码，须满足密码策略 |
| `SESSION_COOKIE_NAME` | 可选 | 默认 `project_risk_session` |
| `CELERY_WORKER_CONCURRENCY` | 可选 | 默认 `2` |
| `SCHEDULER_*` | 可选 | 调度节奏（操作默认值，非 SLO） |
| `LOG_LEVEL` / `SCHEDULER_LOG_LEVEL` | 可选 | 日志级别 |

要点：

- 会话签名密钥**不**放在 `.env.production`，而是由 `init-secrets.sh` 生成的 compose secret 文件注入（见第 5 节）。
- **不要在教程、截图、聊天中粘贴真实密码。** `.env.production` 已被 `.gitignore` 忽略，绝不要提交。
- 部署脚本会拒绝仍含 `replace_with...` 占位符的 `.env.production`。

---

## 5. 初始化 secrets

生成会话密钥与（自签）TLS 证书（gitignored，从仓库根执行）：

```bash
bash infra/scripts/init-secrets.sh
```

该脚本生成：

- `infra/secrets/project_risk_session_key`：会话签名密钥（compose secret，以只读文件挂载到 `/run/secrets/project_risk_session_key`）。
- `infra/proxy/certs/tls.crt`、`infra/proxy/certs/tls.key`：自签 TLS 证书（仅测试级，真实部署请替换为公司 CA 签发的证书，见第 6 节）。

文件权限：

- `infra/secrets/` 与 `infra/proxy/certs/` 目录权限 `700`。
- 会话密钥 `0644`（API 以非 root 的 appuser uid 1001 运行，compose 文件 secret 以宿主文件属主挂载，故需可被该 uid 读取；多租户硬化主机上可 `chown 1001` 并收紧到 `0640`）。
- TLS 私钥 `tls.key` 权限 `600`。

哪些文件必须留在宿主：

- `infra/secrets/project_risk_session_key`
- `infra/proxy/certs/tls.crt`、`tls.key`
- `.env.production`
- 备份 KEK 文件（见第 12 节，建议放在 `/etc/risk/backup-keys/`）

哪些文件绝不能提交：上述全部已被 `.gitignore` 忽略（`infra/secrets/`、`infra/proxy/certs/`、`.env.*`）。**不要**把它们加入 git。

### 备份 KEK（首次部署可先跳过，备份前必须准备）

备份 KEK 与 `DATA_ENCRYPTION_KEY` **相互独立**，仅用于备份信封加密：

```bash
sudo mkdir -p /etc/risk/backup-keys
sudo openssl rand -base64 32 > /etc/risk/backup-keys/backup_kek_v1
sudo chmod 0400 /etc/risk/backup-keys/backup_kek_v1
```

该文件内容**绝不能**被任何脚本读取或打印。备份脚本只引用其路径。

---

## 6. TLS

生产环境**不要关闭 TLS**。`infra/proxy/nginx.conf` 强制 `listen 443 ssl` 并启用 TLSv1.2/1.3 与 HSTS。

### 证书来源

- **内网自签 CA**：用 `init-secrets.sh` 生成的自签证书可直接启动，但浏览器会告警。
- **公司内部 CA**（推荐）：用公司内部 CA 签发证书，CN/SAN 与 `CORS_ORIGIN` 主机名一致。

### 放置位置

无论哪种来源，最终都要落到（覆盖 `init-secrets.sh` 生成的测试证书）：

```
infra/proxy/certs/tls.crt
infra/proxy/certs/tls.key
```

`tls.key` 权限保持 `600`。这两个文件以只读方式 bind-mount 到 proxy 容器 `/etc/nginx/certs/`。

### 浏览器信任 CA

- 自签：浏览器手动导入 `tls.crt` 为受信任根证书（仅内网受控机器）。
- 公司 CA：将公司根 CA 分发到内网客户端的信任库。

---

## 7. 首次部署

确保第 3–6 步完成后，执行：

```bash
./infra/deploy/deploy.sh --seed
```

`--seed` 表示这是首次初始化，会在 migration 后执行 `risk-platform-seed` 创建初始管理员。**不要在后续升级时重复 seed**（seed 不会覆盖已有管理员密码，但通常无需再跑）。

### 脚本内部做了什么

1. 检查 `docker`、`docker compose`、`git`、工程根、`.env.production`、会话密钥、TLS 证书。
2. `docker compose config` 预校验（确保必填变量已设置）。
3. `docker compose build` 构建生产镜像（api + web）。
4. 先启动 `postgres` + `redis`，等待健康。
5. 用一次性容器执行 `docker compose run --rm --no-deps api alembic upgrade head` 应用数据库迁移（**不依赖**已运行的 api 容器；fresh 部署时 api 尚未启动）。
6. 用一次性容器校验数据库已迁移到 alembic head（对比 `alembic heads` 与 `alembic current`）。
7. （`--seed` 时）用一次性容器执行 `risk-platform-seed` 创建初始管理员与参考数据；`INITIAL_ADMIN_*` 显式传入（值不打印，`INITIAL_ADMIN_PASSWORD` 必填，其余缺省时用 seed 默认值）。
8. `docker compose up -d` 启动完整 stack。
9. 调用 `healthcheck.sh` 统一健康检查。
10. 记录已部署 SHA 到 `infra/deploy/.deployed-sha`。

任一步失败立即退出。迁移/seed 一次性容器的成败以命令自身 exit code 为准；api 的 HTTP `unhealthy`（503）状态不会参与判定。部署成功后访问：

```
https://risk.example.internal:8443
```

---

## 8. 登录 / 首次管理员

seed 之后系统存在一个初始管理员：

- **来源**：`deploy.sh --seed` 将 `.env.production` 中的 `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_DISPLAY_NAME` / `INITIAL_ADMIN_PASSWORD` 显式传给一次性 seed 容器（默认用户名 `admin`；seed 从进程环境直接读取，compose 不会把未在 `environment:` 中引用的变量注入容器）。
- **首次密码**：即你在 `.env.production` 中设置的 `INITIAL_ADMIN_PASSWORD`。该密码必须满足密码策略（≥12 位，含大小写、数字、符号，且不含用户名）。
- **mustChangePassword**：初始管理员创建时 `mustChangePassword=true`，首次登录后**必须**修改密码。
- seed 脚本**不会**在控制台输出密码，也**不会**在重复执行时覆盖已有管理员密码。
- 登录后请尽快通过 `POST /api/auth/change-password` 修改密码；修改成功会撤销全部已有会话。

> 本教程不硬编码任何默认密码。初始密码完全由你在 `.env.production` 中设定。

---

## 9. 验证部署

```bash
./infra/deploy/healthcheck.sh
./infra/deploy/status.sh
```

`healthcheck.sh` 成功时输出 `HEALTHCHECK_OK`；任一服务异常则非 0 退出。

curl 示例（在服务器本机）：

```bash
# 直接探测 API（绕过 proxy）
curl -sk https://127.0.0.1:8443/api/health

# 通过 proxy 探测前端
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8443/
```

`/api/health` 返回包含 `version` 与 `timestamp` 的 JSON。

---

## 10. 常用运维

```bash
./infra/deploy/start.sh                 # 启动（不重建）
./infra/deploy/stop.sh                  # 停止（保留卷）
./infra/deploy/restart.sh               # 重启（不重建、不删卷）
./infra/deploy/logs.sh                  # 跟踪全部服务日志
./infra/deploy/logs.sh api              # 跟踪单个服务
./infra/deploy/logs.sh worker scheduler # 跟踪多个服务
./infra/deploy/status.sh                # 状态汇总
./infra/deploy/healthcheck.sh           # 健康检查
```

`stop.sh` / `restart.sh` **拒绝** `--volumes`，绝不执行 `docker compose down -v`。

---

## 10.1. 生成合成测试邮件（Demo 数据）

如需完整业务 Demo，推荐严格按以下顺序执行：

```bash
./infra/deploy/deploy.sh --seed
./infra/deploy/seed-demo-data.sh --confirm-demo-data
./infra/deploy/generate-demo-mails.sh
./infra/deploy/generate-demo-mails.sh --validate
```

`seed-demo-data.sh` 通过现有 Docker Compose 的一次性 API image 容器访问
PostgreSQL，不依赖宿主 `5432`，且只在显式传入 `--confirm-demo-data` 时执行。
它创建 10 个 `WSLDEMO` synthetic users、12 个项目、40 条风险和 72 条待办；
使用稳定 key 重复执行是幂等的，不覆盖 initial admin、不删除业务数据、不 reset
database、不创建真实邮箱账号。风险状态遵守正式模型的 `ACTIVE` / `RESOLVED`
约束，open / monitoring / mitigated / closed 展示阶段记录在合成风险标题和说明中。

```bash
./infra/deploy/generate-demo-mails.sh            # 生成
./infra/deploy/generate-demo-mails.sh --validate # 校验已有目录
```

该脚本在 `artifacts/demo-mails/`（已 gitignore）下生成约 24 封合成测试邮件，覆盖明确风险 / 模糊风险 / 非风险 / 已解决更新 / 长邮件 / 中英混合 / 回复链等场景，并附带真实有效的 `.txt` / `.pdf` / `.docx` / `.xlsx` 合成附件 fixture。所有 Subject 均带 `[WSLDEMO]` 前缀，正文均明确标注「完全合成数据」，项目名称与 demo seed 对齐。

### 重要边界

- 本脚本**只生成测试邮件内容**：不发送邮件、不实现 SMTP、不需要 SMTP 凭据。
- 不修改 mailbox ingest，不向数据库直接插入邮件。
- 你需要**手工**把邮件内容发到已经配置进系统的测试邮箱；系统随后通过真实链路读取：

```
user manual send
      ↓
real mailbox
      ↓
IMAP → scheduler → worker → parser → real Provider
      ↓
candidate / review → user confirmation → Risk / Todo
```

**不要**使用任何绕过这条路径的“快速测试”数据库写入。

### 使用测试邮件

1. 运行 `./infra/deploy/generate-demo-mails.sh`。
2. 打开 `artifacts/demo-mails/`，选择一封 `.md`。
3. 手工复制其中的 `Subject` 与 `---- BODY ----` 下的正文，发送到已配置进系统的测试邮箱。
4. 如测试附件：将 `artifacts/demo-mails/attachments/` 下对应 fixture 手工添加到邮件。
5. 等待 scheduler/mailbox sync，或通过系统现有方式触发同步。
6. 在 UI 中检查 Mail Sync Summary / Messages / Candidate / AI classification /
   Project mapping / Risk category / adjust·ignore·confirm / confirmed Risk /
   Timeline / Audit。

详细推荐发送顺序见 `artifacts/demo-mails/README.md`（第一批先发一封明确风险 + 一封非风险验证基础 AI flow，第二批发模糊与已解决场景，第三批测试附件）。

---

## 11. 更新版本

升级到指定 release：

```bash
./infra/deploy/update.sh <git-tag-or-commit>
```

例如：

```bash
./infra/deploy/update.sh 305706f8f42a6b92223f353e387f0c92dbba643b
```

### 脚本流程

1. 工作树必须 clean（拒绝在未提交改动上部署）。
2. `git fetch --tags origin`。
3. 解析并校验目标 tag/commit 必须存在。
4. 记录当前已部署 SHA（previous）。
5. 建议执行 pre-update backup（默认开启；`--no-backup` 跳过，不推荐）。
6. `git checkout` 明确 SHA（不部署模糊的 branch HEAD）。
7. `docker compose config` 校验。
8. `docker compose build` 重建镜像。
9. `alembic upgrade head`（**仅升级，不自动降级**）。
10. `docker compose up -d` 重建 stack。
11. `healthcheck.sh` 健康检查。
12. 输出 previous SHA / current SHA / 部署结果。

### Rollback 边界

- **代码可以回滚**：`git checkout <previous-sha>` 后重新 `./infra/deploy/deploy.sh`。
- **数据库不会自动降级**：`update.sh` 不执行 `alembic downgrade`，也不假设降级安全。
- 若升级后 health 失败：脚本会报告失败、给出回滚到上一代码 SHA 的命令，并提醒数据库恢复必须使用批准的 backup/restore runbook（先在隔离目标演练，见第 13 节）。

---

## 12. Backup

```bash
./infra/deploy/backup.sh
./infra/deploy/backup.sh --type weekly
./infra/deploy/backup.sh --output /var/backups/risk/manual.rpbk
```

`backup.sh` 是 T036 已批准备份 CLI 的薄 wrapper，不重新实现加密。

### 它做了什么

1. 从 `deploy.conf` 读取 `BACKUP_DIR` / `BACKUP_KEK_VERSION` / `BACKUP_KEK_FILE` / `DOCKER_BIN`；从 `.env.production` 读取 Postgres 凭据（用于备份 DSN，**不打印**）。
2. `docker compose stop api worker scheduler` 静默写路径（postgres + redis 保持运行）。
3. 在 api 镜像中运行 `python -m risk_backup backup ... --quiesce none`，`pg_dump`/`pg_restore` 在 postgres 容器内通过 docker socket 执行（ADR 0031 §12）。
4. `docker compose up -d --no-deps api worker scheduler` 恢复写路径。
5. 解析元数据日志，输出 `backupId` / `status` / artifact 路径 / 大小。
6. 备份非 USABLE 时非 0 退出。

### 概念

- **backup artifact**：单个加密 `.rpbk` 文件，包含 PostgreSQL 快照（`pg_dump -Fc`）+ 持久存储卷归档 + 加密 manifest。
- **backup KEK**：256 位密钥，与 `DATA_ENCRYPTION_KEY` 独立，仅存于宿主只读文件（`/etc/risk/backup-keys/backup_kek_v1`，`0400`）。artifact 只存 wrapped DEK + KEK key version，**不存 KEK 本身**。
- **retention**：ADR 0009 保留策略（7 daily / 4 weekly / 12 monthly）由宿主 cron 或人工运维驱动，**不是**本包职责。删除备份副本受 ADR 0027 `BACKUP_COPY` 谓词门控并写 `BACKUP_COPY_DELETED` 审计；本脚本**不删除备份**。
- **如何判断 USABLE**：CLI 退出码 0 且无 error / unquiesce warning 即 USABLE；脚本据此判断并退出码反映。

### 前置条件

- `BACKUP_KEK_FILE` 存在且可读（`0400`）。
- `DOCKER_BIN`（默认 `/usr/bin/docker`）存在。
- stack 正在运行（postgres + 待静默的写路径服务）。
- `BACKUP_DIR` 目录已创建（`mkdir -p /var/backups/risk`）。

### 安全提示

- 备份容器以 root 运行并挂载 docker socket（等价于宿主 root）。仅在可信 operator 手动执行时使用。
- 脚本不读取 / 打印 KEK 内容，不 `set -x`。

### 重要

**backup ≠ restore-tested**。一个备份只有在成功通过隔离 restore drill 后才算有效（ADR 0009）。见第 13 节。

---

## 13. Restore drill（隔离恢复演练）

**不要直接在生产数据库运行 restore drill。**

```bash
./infra/deploy/restore-drill.sh \
  --artifact /var/backups/risk/daily-20260815T120000Z.rpbk \
  --target-db restore_drill \
  --target-storage /var/tmp/restore-drill/storage \
  --confirm-isolated \
  --prepare
```

说明：

- `--target-db` 必须是**隔离的空数据库名**，且**不得**等于生产数据库（脚本 fail-closed 拒绝）。
- `--target-storage` 必须是**隔离的空目录**绝对路径，不得是 `/app/storage`。
- `--confirm-isolated` 必须显式传入，确认目标是隔离的、非生产。
- `--prepare` 会创建隔离空数据库并清空 / 创建存储目录；不传则要求两者已存在且数据库为空。

### 它做了什么

调用现有 T036 restore 实现到隔离目标，验证：KEK 解包、payload 解密、manifest 校验、组件 sha256、`pg_restore` 到空库、**审计哈希链校验**、alembic-head 匹配、文件 extract、orphan/missing reconcile。任一失败即 fail-closed 中止，无部分成功。

脚本输出审计记录总数 / 已验证数、reconcile 引用/存在/孤儿/缺失计数。演练成功后给出清理隔离目标的命令。

---

## 14. 故障排查

通用排查命令：

```bash
./infra/deploy/status.sh
./infra/deploy/healthcheck.sh
./infra/deploy/logs.sh <service>
```

### PostgreSQL unhealthy

```bash
./infra/deploy/logs.sh postgres
docker exec project-risk-postgres pg_isready -U project_risk -d project_risk
docker volume inspect project-risk-postgres-data
```

常见：磁盘满、卷权限、密码与 `.env.production` 不一致（首次初始化后改密码需重建数据库或同步配置）。

### Redis unavailable

```bash
./infra/deploy/logs.sh redis
docker exec project-risk-redis-app redis-cli ping
```

Redis 非持久、不是事实源；重启即可，不影响业务数据（scheduler reconciler 会补齐）。

### API unhealthy

```bash
./infra/deploy/logs.sh api
docker exec project-risk-api python -c 'import urllib.request;print(urllib.request.urlopen("http://127.0.0.1:3000/api/health",timeout=3).status)'
```

检查 `DATABASE_URL`、`DATA_ENCRYPTION_KEY`、`CORS_ORIGIN` 是否正确，migration 是否已执行。

### worker offline

```bash
./infra/deploy/logs.sh worker
docker exec project-risk-worker celery -A risk_platform.reliability.celery_app:celery_app inspect ping -d celery@worker1 --timeout=5
```

常见：Redis 不可达、依赖未就绪。

### scheduler second-active / fail-fast

```bash
./infra/deploy/logs.sh scheduler
docker exec project-risk-scheduler python -c 'import urllib.request,json;d=json.load(urllib.request.urlopen("http://127.0.0.1:9191/",timeout=3));print(d)'
```

若 `lock_held=false`：说明另一个 scheduler 持有 advisory lock，本实例 fail-fast 退出后会重试接管。确认没有重复的 scheduler 实例（不要手动额外启动 scheduler）。

### scheduler unhealthy

`healthy=false`：lock 未持有或最近一次 tick 超过 liveness window。检查 PostgreSQL 连接与 scheduler 日志。

### proxy 502

```bash
./infra/deploy/logs.sh proxy
./infra/deploy/logs.sh api
curl -sk https://127.0.0.1:8443/api/health
```

常见：api 未健康、`api` 服务未就绪、网络问题。proxy 依赖 `api` service_healthy。

### SSE 被 nginx buffering

`infra/proxy/nginx.conf` 已对 `^/api/agent/conversations/.+/events$` 设置 `proxy_buffering off` / `proxy_cache off` / 长超时。若 SSE 不工作，确认该 location 块未被改动、且请求路径匹配该正则。

> 注意：INTERNAL_MVP 下 SSE 初始事件 ≤2s 与 heartbeat/keepalive **未认证**（见第 16 节）。

### migration failure

```bash
./infra/deploy/logs.sh api
docker compose --env-file .env.production -f infra/docker-compose.yml run --rm --no-deps api alembic current
docker compose --env-file .env.production -f infra/docker-compose.yml run --rm --no-deps api alembic upgrade head
```

migration 失败不要盲目降级；必要时从备份恢复（隔离演练验证后）。

### disk full

```bash
df -h
docker system df
```

清理构建缓存 `docker builder prune`（不要删业务卷）。

### volume permissions

```bash
docker volume inspect project-risk-postgres-data
docker volume inspect project-risk-storage
docker exec project-risk-api ls -la /app/storage
```

storage 卷首次挂载继承 appuser（uid 1001）属主。如出现权限错误，确认容器以 uid 1001 运行、卷未被外部改动。

### secret file permissions

```bash
ls -la infra/secrets/project_risk_session_key infra/proxy/certs/tls.key
```

会话密钥需可被 uid 1001 读取（`0644` 或 `chown 1001` + `0640`）；TLS 私钥 `600`。`.env.production` 应 `600`。

---

## 15. 数据目录 / volume

| 卷 | 用途 |
|----|------|
| `project-risk-postgres-data` | PostgreSQL 数据（唯一持久事实源） |
| `project-risk-storage` | 应用持久存储（Excel 导入原件、邮件等） |

这两个卷是业务数据的载体。

> ⚠️ **警告**：`docker compose down -v` 会**删除命名卷**，导致 PostgreSQL 数据与持久存储**永久丢失**。生产环境**绝不要**随意执行。本部署套件的 `stop.sh` / `restart.sh` / `update.sh` **从不**使用 `-v`。如确需删除卷（例如彻底销毁环境），必须人工执行并完全清楚数据丢失后果。

仅开发 / 测试重建数据时才考虑删除卷，且必须先确认无生产数据。

---

## 16. INTERNAL_MVP 已知限制

原样保留 ADR 0033 的限制。INTERNAL_MVP 面向**低并发内部使用**，**未通过 capacity 认证**（NOT capacity-certified）。以下 findings 不作为 INTERNAL_MVP 阻断项，但仍是 PRODUCTION_CAPACITY_READY milestone 的正式阻断项：

- **low-concurrency internal use**：仅适用于低并发内部使用，不作为面向公网 / 高并发 / 外部 Provider 的生产发布。
- **NOT capacity-certified**：任何对外材料不得声称已通过 ADR 0032 50-VU capacity 认证；T038 保持 `REVIEW_FAILED / DEFERRED_FOR_INTERNAL_MVP`。
- **T038 deferred**：full 50-VU ADR 0032 capacity 认证（T038 重跑）延后。
- **SSE 初始事件 p95 ≤ 2s 未认证**（跨 ADR 0030 §3 drain 5s vs ADR 0032 §6 ≤2s 张力，会话创建不写同步 `AgentEvent`）。
- **SSE heartbeat / transport keepalive 未认证**（`_stream` 无 keepalive；fast-fail 流 ~5s 关闭无可测长流）。
- **Provider deterministic test seam deferred**（ADR 0032 §9 fake Provider 无 production 注入点，`build_provider` 无条件）。
- **connection-pool 调优 / 剩余 50-VU capacity gates deferred**（单 uvicorn + SQLAlchemy 默认池在 50 VU 下饱和；slow-query / 连接饱和 / lock wait，待 T038 重跑）。

在这些限制解决（PRODUCTION_CAPACITY_READY）之前，不要将本部署用于公网 / 高并发 / 外部 Provider 场景。
