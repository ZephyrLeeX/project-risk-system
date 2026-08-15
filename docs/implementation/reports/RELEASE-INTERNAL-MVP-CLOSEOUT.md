# INTERNAL_MVP Release Closeout — 最终 readiness 核验

- **Release profile:** `INTERNAL_MVP`（ADR 0033）
- **核验日期:** 2026-08-15
- **核验基线（HEAD）:** `60f59fb2c88fedaab7fcb014494563ca019796f9`
- **release-policy checkpoint（ADR 0033）:** `5bb724a6cf54208fd9305d241130beebb8c611a1`
- **SHA-record commit:** `60f59fb docs: record ADR 0033 release-policy checkpoint SHA (5bb724a)`
- **执行依据:** `docs/implementation/ORCHESTRATOR.md` + ADR 0033
- **本次范围:** 仅 release closeout 核验与文档；**不执行新 implementation Task；不修改 production code**

> 本报告为发布交接核验记录。状态枚举、路径、命令、SHA 保持原样。

## 0. 核验结论（TL;DR）

**INTERNAL_MVP release readiness = READY（无 release blocker）。**

- required gates 全部与 ADR 0033 §1 一致且已通过。
- T038 保持 `REVIEW_FAILED` / `DEFERRED_FOR_INTERNAL_MVP`（未改 PASS、未声称 certified）。
- T039 保持 `DEFERRED` / `BLOCKED_EXTERNAL_INPUTS`（未启动）。
- Wave 25 = capacity-track deferred / closed-for-MVP（非 Integration PASS）。
- 部署 / 备份恢复 / health checks / 契约冻结均可执行；无 secret/cert/KEK 泄漏。
- 非阻断观察 2 项（proxy/web 无独立 healthcheck；操作员联系人由部署方自定义）—— 非
  release blocker，可后续 hardening。

**Release recommendation:** 可按 INTERNAL_MVP（低并发内部使用）发布。不得作为公网 / 高并发 /
外部 Provider 生产发布。

## 1. repository working tree clean

- `git status`：working tree clean（HEAD `60f59fb`，branch `main`，ahead of origin by 14）。
- release closeout 期间唯一改动为本报告 + 两份 release 文档（见第 14 节），**无 production
  code 改动**。
- `pnpm contracts:check` 重新导出 OpenAPI + 重新生成 `openapi.ts` 后 `git status` 仍 clean
  （frozen artifact zero diff，见第 8 节）。

## 2. INTERNAL_MVP required gates 与 ADR 0033 一致

逐项核验 `EXECUTION_STATE.md` / `TASK_GRAPH.md` / Wave reports，与 ADR 0033 §1 必需 gate
完全一致：

| ADR 0033 §1 必需 gate | 仓库状态 | 一致 |
|---|---|---|
| 功能验收：Wave 1–24 `PASS`；T001–T047 `REVIEW_PASSED` | EXECUTION_STATE Completed waves 1–24 PASS；Completed tasks T001–T047 全 REVIEW_PASSED | ✓ |
| 安全 / 授权（T037 suite） | T037 `REVIEW_PASSED`（Wave 24 PASS） | ✓ |
| 审计（哈希链 + append-only trigger） | T037 `REVIEW_PASSED` | ✓ |
| 可靠性（幂等 enqueue / crashed-worker / orphan / outbox / 回滚原子） | Wave 16/21/22 PASS | ✓ |
| 备份 / 恢复（T036 + PG16 drill；RPO 24h / RTO 4h） | T036 `REVIEW_PASSED`（Wave 23 PASS） | ✓ |
| 部署（T035 单机 Compose，无 NestJS/Prisma runtime） | T035 `REVIEW_PASSED`（Wave 22 PASS） | ✓ |
| T047 `/api/todos` 分页 remediation `REVIEW_PASSED` | T047 `REVIEW_PASSED`，checkpoint `425c0fd` | ✓ |

真实性约束核验：

- T038 = `REVIEW_FAILED` / `FAIL`，disposition `DEFERRED_FOR_INTERNAL_MVP`（TASK_GRAPH 第
  140 行；未改 PASS）。✓
- T039 = `BLOCKED_EXTERNAL_INPUTS` / `DEFERRED`，非 INTERNAL_MVP 必需项（TASK_GRAPH 第 141
  行；未启动）。✓
- Wave 25 = capacity-track deferred / closed-for-MVP，**非** Integration `PASS`
  （TASK_GRAPH Wave 表第 94 行）。✓
- 无任何文件声称 capacity-certified（T047 报告 §「停止边界」明确 disclaim）。✓
- checkpoint SHA 全部存在：`425c0fd`、`5bb724a`、`60f59fb`、`9adc487`（`git cat-file -e`
  全 OK）。✓

## 3. production Compose / env.example / secrets / TLS / startup docs 可执行

- `project-risk-system/infra/docker-compose.yml`：7 服务（postgres:16-alpine / redis:7-alpine
  / api / worker / scheduler / web / proxy:nginx），api/worker/scheduler 共享
  `risk-platform-api:0.1.0` 镜像；必填 env 用 `${VAR:?...}` fail-fast（`POSTGRES_PASSWORD`、
  `DATA_ENCRYPTION_KEY`、`CORS_ORIGIN`）；postgres 仅 `127.0.0.1` host bind；proxy 为唯一
  对外 edge（`:8443`）；TRUSTED_PROXY_CIDRS=10.30.0.0/24 固定子网；持久卷
  `project-risk-postgres-data` + `project-risk-storage`；compose secret
  `project_risk_session_key`（read-only file）。✓
- `project-risk-system/infra/env.example`：完整占位符模板，含 runtime/edge、PostgreSQL、
  secrets、scheduler cadence、initial admin；无真实 secret。✓
- `project-risk-system/infra/scripts/init-secrets.sh`：生成 session key（≥48 字节）+ 自签名
  TLS 证书，输出 gitignored；提示创建 `.env.production`。✓
- TLS：`infra/proxy/nginx.conf` 终止 TLS（TLSv1.2/1.3），安全头（HSTS / X-Frame-Options /
  nosniff / Referrer-Policy），SSE `proxy_buffering off`，`/api` → api、`/` → web。✓
- startup docs：`project-risk-system/infra/README.md` 含 First-time init / Operate / SSE /
  single-active scheduler / Out of scope 段，命令可执行。✓

## 4. database migration + seed/admin bootstrap 步骤明确

`infra/README.md`「First-time init」明确（应用启动不创建 schema，ADR 0010）：

```bash
docker compose --env-file .env.production -f infra/docker-compose.yml exec api alembic upgrade head
docker compose --env-file .env.production -f infra/docker-compose.yml exec api risk-platform-seed
```

- seed 幂等：四角色 / 权限 / 参考数据 + 初始管理员（`INITIAL_ADMIN_USERNAME` /
  `INITIAL_ADMIN_PASSWORD`，env.example 第 44–47 行）。✓
- Alembic head regression 此前 Wave 已验证（PostgreSQL 16 + Alembic head 通过）。✓

## 5. backup / restore one-shot runbook 可用

- `project-risk-system/infra/backup/README.md`：完整 one-shot runbook，含 quiesce →
  pg_dump -Fc → file tar → manifest → AES-256-GCM envelope → cleanup → unquiesce；
  restore fail-closed isolated target（KEK lookup → AEAD → manifest sha256 → pg_restore →
  audit hash-chain → alembic-head → file reconcile；partial-set abort）。✓
- 备份 KEK 独立于 `DATA_ENCRYPTION_KEY`，宿主机 read-only 文件加载，不入 env / 不提交。✓
- ADR 0009 RPO 24h / RTO 4h；restore drill 步骤明确（隔离空 DB + 空 storage）。✓
- T036 已通过真实 PostgreSQL 16 drill `REVIEW_PASSED`。✓

## 6. health checks

| 服务 | healthcheck | 核验 |
|---|---|---|
| PostgreSQL | `pg_isready`，5s | ✓ docker-compose.yml:36-43 |
| Redis | `redis-cli ping`，5s | ✓ docker-compose.yml:56-61 |
| API | `GET /api/health`，10s | ✓ docker-compose.yml:90-97 |
| worker | `celery inspect ping`，30s | ✓ docker-compose.yml:117-122 |
| scheduler | `:9191` liveness，10s | ✓ docker-compose.yml:155-162 |
| proxy | 无独立 healthcheck | ⚠ 非阻断（depends_on api healthy + web started） |
| web | 无独立 healthcheck | ⚠ 非阻断（静态 SPA，service_started） |

proxy/web 无独立 healthcheck 为**非阻断观察**，非 INTERNAL_MVP release blocker（proxy 仅在
api healthy 后启动；web 仅服务静态资源）。可后续 hardening 补 nginx 探活。

## 7. `/api/todos` 使用 T047 paginated contract

- `todos/api.py`：`GET /todos` → `service.list(identity, query)`，返回
  `ApiResponse[ManagerTodoListResponse]`。✓
- `todos/schemas.py`：`ListTodosQuery` 含 `page`（default 1, ge 1）/ `pageSize`（default 20,
  ge 1, le 100，`extra="forbid"`）；`ManagerTodoListResponse` 含 `page`/`pageSize`/`total`
  （+ 既有 items/summary/owners/schedule/updatedAt/dataScope，additive）。✓
- `todos/service.py`：SQL-layer 分页 —— items 查询
  `.offset((page-1)*pageSize).limit(pageSize)`，`total` 由独立
  `select(func.count(ActionItem.id))` 产生；无全量物化 + Python slice；`id.asc()` 确定性
  tiebreaker。✓
- 契约与 `RiskQuery` canonical 分页一致；OpenAPI 已 re-freeze（93 paths / 243 schemas）。
  ✓
- T047 `REVIEW_PASSED`（checkpoint `425c0fd`），Independent Review `APPROVE-WITH-NITS`
  无 blocking finding。✓

## 8. frozen OpenAPI / generated frontend types zero diff

- 执行 `pnpm contracts:check`（= `contracts:sync` export+gen + `git diff --exit-code`）：
  - `uv run risk-platform-openapi` 导出 `packages/contracts/openapi/openapi.json`。
  - `openapi-typescript` 生成 `packages/contracts/src/generated/openapi.ts`。
  - `git diff --exit-code -- packages/contracts/openapi packages/contracts/src/generated`
    → **exit 0，zero diff**。✓
- 统计：93 paths / 243 schemas（与 Wave 18/22/24 冻结基线一致）。✓
- 重新生成后 `git status` clean（frozen artifact 确定性）。✓

## 9. release notes 明确写出 limitations

`docs/implementation/release/INTERNAL-MVP-RELEASE-NOTES.md` 第 3 节如实列出（逐项核验）：

1. INTERNAL_MVP = 低并发内部使用 ✓
2. NOT capacity-certified ✓
3. T038 remains FAIL / deferred ✓
4. SSE initial-event ≤2s 未认证 ✓
5. SSE heartbeat/keepalive 未认证 ✓
6. deterministic Provider capacity-test seam 未完成 ✓
7. connection-pool / 剩余 50-VU capacity gates deferred ✓

## 10. limitations 未被描述成已解决

- release notes 第 3 节每项均标注「未认证 / 未完成 / 未调优 / 未通过 / deferred」，并明示
  「不得描述为已修复 / 已认证 / 已通过」。✓
- T047 分页修复明确说明「这不等于 T038 PASS」。✓
- 无任何 release 文档声称 capacity-certified。✓

## 11. 升级到 PRODUCTION_CAPACITY_READY 路径明确

release notes 第 4 节 + operator handoff 第 10 节按序写出：

1. 处理 deferred findings（或正式 ADR 重新决策，不得静默 waive）✓
2. 重新执行 T038（ADR 0032 reference env / methodology）✓
3. T038 PASS 后（连续 2 次 hard gate PASS + 证据完整）✓
4. 准备真实 external inputs（真实邮箱 / Provider / TLS / 恢复演练窗口）✓
5. 执行 T039（按 contract 验收）✓

## 12. rollback / restore / operator contact procedure 明确

- rollback / restore：operator handoff 第 7 节 = 从加密备份恢复到隔离目标后切换，fail-closed
  任一步 abort 不得使用；镜像 / 代码回滚 = `git checkout 60f59fb` + rebuild。✓
- operator contact：handoff 第 8 节明确「联系人渠道由部署组织自定义，不写入仓库」，
  建议内部记录部署负责人 / KEK 保管人 / 恢复演练窗口 / on-call。✓
- 审计哈希链（ADR 0008）提供事后可核查轨迹。✓

## 13. 无 test secrets / certs / `.env` / backup KEK / 明文数据纳入 release artifact

对 git-tracked 树扫描：

- `git ls-files` 中 `.env*` 仅 `.env.example`（占位符 `change_me_for_local_development` /
  `replace_with_32_byte_base64_key`，无真实 secret）。✓
- `infra/proxy/certs/tls.crt|tls.key` 与 `infra/secrets/project_risk_session_key`：
  **gitignored**（`.gitignore` 含 `infra/secrets/`、`infra/proxy/certs/`），仅本机磁盘，
  未 tracked。✓
- 无 `BEGIN PRIVATE KEY` / `BEGIN CERTIFICATE` 进入 tracked 树（`git grep` NONE）。✓
- 无 backup KEK 文件 tracked（KEK 宿主机 `/etc/risk/backup-keys/`，不入仓库）。✓
- 无明文 backup / 邮件正文 / 业务数据 tracked。✓
- `init-secrets.sh` 输出全部 gitignored。✓

## 14. release artifacts / docs status

本次生成（release closeout deliverable，非 feature Task）：

- `docs/implementation/release/INTERNAL-MVP-RELEASE-NOTES.md`（新建）— 发布说明 +
  limitations + 升级路径。
- `docs/implementation/release/INTERNAL-MVP-OPERATOR-HANDOFF.md`（新建）— 操作员交接 +
  部署 / 备份恢复 / health / rollback / contact。
- `docs/implementation/reports/RELEASE-INTERNAL-MVP-CLOSEOUT.md`（本文件，新建）— 最终
  readiness 核验。

既有关键文档（未修改）：

- `docs/adr/0033-...md`（release profiles 定义）
- `project-risk-system/infra/README.md`、`docker-compose.yml`、`env.example`、
  `scripts/init-secrets.sh`、`proxy/nginx.conf`
- `project-risk-system/infra/backup/README.md`
- `docs/implementation/reports/T038.md`、`T047.md`、`WAVE-25-PARTIAL.md`

> 仓库无既有正式 release checklist / release Task，故按授权仅生成上述 closeout report /
> release notes / operator handoff，未创建新 feature Task。

## 15. 本次未执行（按授权边界）

- 未重跑 T038 50-VU certification。
- 未执行 T039。
- 未优化 SSE / connection pool。
- 未修改 ADR 0032 thresholds。
- 未将 T038 改 PASS。
- 未启动 capacity / external track。
- 未修改 production code。
- 未创建新 implementation Task。

## 16. blockers

**无 INTERNAL_MVP release blocker。**

非阻断观察（可后续 hardening，不阻断本发布）：

1. proxy / web 无独立 healthcheck（proxy 依赖 api healthy；web 静态资源）。
2. operator 联系人渠道由部署组织自定义，未写入仓库（设计如此，非缺陷）。

## 17. Release recommendation

**建议发布 INTERNAL_MVP（低并发内部使用）。**

- 全部必需 gate 通过且与 ADR 0033 一致；T038/T039 真实性约束保持；部署 / 备份恢复 /
  health / 契约冻结均可执行；无 secret 泄漏；无 release blocker。
- 发布后须遵守：不暴露公网 / 高并发 / 外部 Provider；不声称 capacity-certified；
  deferred findings 进入 PRODUCTION_CAPACITY_READY milestone 处理。
