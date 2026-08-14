# Wave 22 Integration

- **Wave:** 22
- **Task:** T035 — Build production Compose and HTTPS proxy topology
- **结果:** `PASS`（无 integration fix）
- **T035 状态:** `REVIEW_PASSED`（code checkpoint `732adfb0c742f486748cc1aff707231b07820b34`）
- **Integration checkpoint:** 见 `EXECUTION_STATE.md`（本条目之后的提交）
- **DG-05 / DG-08:** 未处理（out of scope）

## 结论

T035 `REVIEW_PASSED` + code checkpoint 后执行项目级联合验证，重点验证 production deployment topology 的项目级联合行为。7-service Compose 全栈（PostgreSQL 16、Redis 7、FastAPI API、Celery worker、ADR 0030 single-active scheduler、Vue 前端、TLS 反向代理）在真实容器下联合一致，durable-task 端到端经请求路径（仅写 PostgreSQL）→ scheduler outbox drain → Redis/Celery 投递 → worker 消费 → PostgreSQL durable 状态 `SUCCEEDED` 完整闭环，scheduled mailbox 路径与 proxy/frontend/SSE/persistence/secrets 行为均通过。T035 deployment write-set 未暴露任何 integration failure，无需 integration fix。scheduler fail-fast SQLAlchemy 连接警告经真实 triage 确认为 frozen T046 进程退出的 cosmetic warning（无真实 resource leak / deployment failure），按协议不越界修改 T046。frozen write-set（`scheduler.py`/`composition.py`/`main.py`/`worker.py`/`celery_app.py`）与 frozen OpenAPI authority 自 `732adfb` 未修改。`infra/backup/**` 保持未触碰，DG-08 不在本 Wave 解决；DG-05 numeric thresholds 未做额外决策。

## 联合行为验证矩阵

以 gitignored `.env.t035-test`（真实 test secrets）+ `init-secrets.sh` 生成的 test cert 启动的 7-service 栈验证（栈在验证期间保持运行）：

| # | 验证项 | 结果 |
|---|---|---|
| 1 | `docker compose config` 渲染（test env + dev `.env`） | PASS（两 env 均 valid） |
| 2 | 7-service 全栈（postgres/redis/api/worker/scheduler/web/proxy） | PASS（7 服务全部 Up，PG/Redis/API/worker/scheduler healthy） |
| 3 | api/worker/scheduler 共享同一 production image | PASS（三者均 `risk-platform-api:0.1.0`，同一 image id `c87ed53…`） |
| 4 | PostgreSQL 16 health | PASS（`pg_isready` accepting connections） |
| 5 | Redis 7 health（仅 broker） | PASS（`redis-cli ping` → PONG） |
| 6 | API health direct `:3000/api/health` | PASS（200，valid JSON） |
| 7 | API health via proxy `/api/health` | PASS（200） |
| 8 | real Celery worker ping + task 执行 | PASS（`celery inspect ping -d celery@worker1` → OK, 1 node online；durable task 端到端 SUCCEEDED） |
| 9 | scheduler T046 entrypoint 运行 + health/liveness | PASS（liveness `:9191` `{"healthy":true,"lock_held":true}`；tick 在 2× outbox drain 窗口内） |
| 10 | scheduler single-active 第二实例 fail-fast | PASS（第 2 实例 `advisory lock 已被持有` → `exit 1`；primary 不受影响） |
| 11 | durable-task 端到端（request path 仅写 PG → scheduler drain → broker → worker consume → PG SUCCEEDED） | PASS（`enqueue_task` 写 `DurableTask` QUEUED + `TaskOutbox` unpublished；scheduler `publish_outbox` 置 `publishedAt`；worker claim→handle→`finish_task`；`status=SUCCEEDED`，`completedAt` 已置，`attemptCount=1`，无 `failureCode`） |
| 12 | scheduled mailbox path（scheduler → `schedule_enabled_syncs`） | PASS（scheduler 三类 action 在 cadence 上运行，lifetime 0 `动作失败`；`schedule_enabled_syncs` 无 enabled config 时 idempotent no-op；with-data 路径已于 Wave 21 验证） |
| 13 | proxy → frontend（`GET /`） | PASS（200, `text/html`） |
| 14 | proxy → `/api`（`GET /api/health`） | PASS（200, valid JSON） |
| 15 | proxy → Agent SSE 路由可达 + 无 buffering | PASS（SSE 路径经 proxy 返回 API 401 auth 而非 502/404，证明 regex location 先于 `/api/` prefix 路由；`nginx -t` valid；SSE location `proxy_buffering off`/`proxy_cache off`/`3600s`/`chunked`） |
| 16 | persistence：PostgreSQL 数据跨 service/container restart | PASS（`down`→`up` 保留 named volume；admin count 1→1，e2e durable task SUCCEEDED 行保留） |
| 17 | persistence：app/import persistent storage 挂载 | PASS（named volume `project-risk-storage` → `/app/storage`，api+worker 均挂载；storage marker 文件跨 restart 保留） |
| 18 | secrets：Compose/env/file-secret wiring | PASS（session key 经 compose `secrets` 只读文件挂载 `ro=false`(RW=false)；runtime 可读） |
| 19 | secrets：无 secret literal 泄漏 | PASS（compose/env/example 无硬编码 secret；`infra/secrets/`、`infra/proxy/certs/`、`.env.t035-test`、`.env` 均 gitignored、未 tracked） |
| 20 | startup/restart dependencies + healthchecks | PASS（7× `service_healthy` + 1× `service_started`；api/worker/scheduler 等 PG+Redis healthy；proxy 等 api healthy） |
| 21 | FastAPI frozen OpenAPI 与 tracked artifact byte-identical | PASS（fresh export `cmp` zero diff，422281 bytes；93 paths / 243 schemas / 104 operations） |
| 22 | 无 SQLite / NestJS / Prisma runtime / dual-write | PASS（api image 仅 Python 无 node；web runtime 仅 nginx+静态无 node/npm；`send_task` 仅在 `dispatcher.publish_outbox`，请求路径不碰 broker，PostgreSQL 唯一 authority） |
| 23 | `infra/backup/**` 未触碰 / DG-08 不在本 Wave | PASS（`infra/backup` 不存在；未创建/修改任何 backup 内容） |
| 24 | DG-05 numeric thresholds 未做额外决策 | PASS（cadence 为 operational defaults，非 SLO；未引入/修改任何 numeric threshold） |
| 25 | scheduler fail-fast SQLAlchemy 连接警告 triage | PASS（见下节；cosmetic，无真实 leak/deployment failure，不修改 T046） |

### durable-task 端到端详情

在 api 容器内以 production `enqueue_task`（请求路径函数）创建 `RETENTION_CLEANUP`（dry_run）任务，验证完整闭环：

1. **请求路径仅写 PostgreSQL**：`enqueue_task` 同事务写 `DurableTask`（`QUEUED`）+ `TaskOutbox`（`publishedAt=None`），不碰 broker。
2. **scheduler outbox drain**：scheduler `publish_outbox`（5s cadence）`celery.send_task` 后置 `TaskOutbox.publishedAt`（事务提交后发布；send 失败保持未发布）。
3. **Redis/Celery 投递 + worker 消费**：real Celery worker 收到 `risk_platform.reliability.execute` → fenced `claim_task` → handler → `finish_task`。
4. **PostgreSQL durable 状态完成**：`DurableTask.status=SUCCEEDED`、`completedAt` 已置、`attemptCount=1`、`failureCode=None`。

该路径证明 PostgreSQL 为唯一 authority，Redis/Celery 仅为 delivery/execution transport，无 DB/Celery dual-write。

### scheduler fail-fast SQLAlchemy 连接警告 triage

Independent Review 提到的 scheduler fail-fast 路径 SQLAlchemy "non-checked-in connection" warning，本轮以真实第 2 scheduler 实例复现并 triage：

- **fail-fast 行为正确**：第 2 实例 `pg_try_advisory_lock` 返回 `False` → `Scheduler.run()` 记录 "advisory lock 已被持有" → 返回 `exit 1`，进程退出。compose `scheduler` service 仅定义单实例，故无 restart-loop 风险。
- **无 advisory lock 泄漏**：`pg_locks` 恰好一行 advisory lock（primary 持有，pid 40，classid=1919513451/objid=1601397608/objsubid=1）；两次 fail-fast 退出后仍仅一行。第 2 实例从未获取 lock。
- **无 connection 泄漏**：`pg_stat_activity` 对 `project_risk` db 连接数在两次 fail-fast 退出后稳定为 4（api/worker/scheduler/psql），未增长。
- **primary 不受影响**：fail-fast 后 primary liveness 仍 `{"healthy":true,"lock_held":true}`。
- **根因**：fail-fast 路径 `run()` 返回 1 时未调用 `release()`（唯一显式 `close()` `lock_conn` 的路径），`async_main` finally 调 `dispose_database_engine` 但该 IDLE 连接在进程退出时由 GC 回收，触发 SQLAlchemy 警告。进程即将终止，PostgreSQL 在连接/进程终止时自动释放 session-level lock，无持久 leak。

**结论**：仅为 frozen `scheduler.py`（T046 write-set）进程退出的 cosmetic warning，**不构成真实 resource leak / deployment failure**。按本次授权与协议，不越界修改 T046；归属 T046 follow-up，非 T035 integration blocker。

## Validation

- `docker compose config`：PASS（test env + dev env）。
- production image build：PASS（api `risk-platform-api:0.1.0`、web `risk-platform-web:0.1.0` 已构建并在栈中运行）。
- clean-stack boot：PASS（`down`→`up` 全栈恢复 healthy）。
- service health / API health：PASS。
- real Celery worker ping + task execution：PASS（1 node online；durable task SUCCEEDED）。
- scheduler health + lock smoke：PASS（liveness 200；single-active fail-fast `exit 1`）。
- proxy frontend/API/SSE validation：PASS（frontend 200、`/api` 200、SSE 路由 401-auth 可达 + `proxy_buffering off`）。
- persistence restart test：PASS（PG data + storage volume 跨 restart 保留；挂载正确）。
- `pnpm contracts:check`（frozen OpenAPI authority）：PASS（sync 后 clean-tree `git diff --exit-code` zero diff；93 paths / 243 schemas / 104 operations 不变）。
- `@risk-platform/contracts` typecheck：PASS（exit 0）。
- `@risk-platform/web` typecheck（`vue-tsc -b --noEmit`）：PASS（exit 0）。
- Ruff：PASS（`All checks passed!`）。
- mypy：PASS（`Success: no issues found in 187 source files`）。
- full backend pytest（PostgreSQL 16 + Redis 7）：PASS（`304 passed, 1 skipped`，102.07s；与 Wave 21 baseline 一致。1 skip 为 `test_audit_query_export` 既有 DB 专项分工提示——skip 信息明确 "TEST_DATABASE_URL 已配置；PostgreSQL 集成由数据库专项测试执行"，非 DB-dependent skip-as-PASS；`test_postgresql_schema.py` 7 + `test_schema_metadata.py` 5 + scheduler/retention/auth 等 DB 专项均真实执行并通过）。
- `uv lock --check`：PASS（`Resolved 65 packages`）。
- `git diff --check`：PASS（clean）。
- frozen write-set 未修改：PASS（`scheduler.py`/`composition.py`/`main.py`/`worker.py`/`celery_app.py` 自 `732adfb` diff 0 行）。

## Integration fix

无。T035 deployment write-set（`infra/docker-compose.yml`、`infra/proxy/**`、`apps/api-python/Dockerfile`、`apps/web/Dockerfile`、env examples、deployment docs）未暴露任何 integration failure。未修改任何 production 代码、测试基线、migration、contract artifact、frozen write-set 或 frozen OpenAPI authority。

## 停止边界（按本次授权与协议）

- **已执行** Wave 22 Integration 项目级联合验证，结果 `PASS`，无 integration fix。
- **未启动** 下一 Wave（Wave 23 / T036）。
- **未执行** T036。
- **未处理** DG-05（performance/reliability numeric thresholds）、DG-08（backup encryption/key/consistency）。
- **未修改** 任何 frozen write-set（T046 `scheduler.py`、T040 `composition.py`/`main.py`/`worker.py`/`celery_app.py`）或 frozen OpenAPI authority。
- **未写入** `infra/backup/**`。

## Wave 23 / T036 readiness 同步（仅 metadata）

T036（deps T031、T035）的 dependency 层现已全部满足：T031 `REVIEW_PASSED`、T035 `REVIEW_PASSED` + Wave 22 Integration `PASS`。但 **T036 自身仍为 `DESIGN_GAP`（DG-08）**：backup encryption/key format/rotation interface 与 PostgreSQL/file consistency 机制尚未由 ADR 批准，本轮不处理 DG-08。因此 T036 **未就绪执行**（`DESIGN_GAP`，非 `READY`）；Wave 23 未启动。详见 `EXECUTION_STATE.md`。
