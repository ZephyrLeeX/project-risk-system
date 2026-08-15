# 0032 — 定义容量与韧性验收阈值（DG-05 resolution）

## Status

Accepted — resolves DG-05。

本 ADR 为 T038（容量与韧性验收）提供批准的数值性能/可靠性阈值与发布门（release gate）语义。
ADR 0009 已定义容量基线（≤300 用户 / 5,000 项目 / 每周 1,000 封周报）、RPO（24h）与 RTO（4h），
但**明确不含 API/任务性能的 PASS/FAIL 阈值**。本 ADR 在不重开 ADR 0009 基线、不重开 ADR 0031
备份语义的前提下，补充 T038 所需的可度量操作/发布判据。

## Context

DG-05 记录：「Numeric latency/throughput/failure-recovery acceptance thresholds at the approved
capacity baseline」。T038 在 T037 `REVIEW_PASSED` + Wave 24 Integration `PASS` 后仅因 DG-05 未决而保持
`BLOCKED_DESIGN_GAP`。ADR 0009 只给数据集规模与 RPO/RTO，无 API p50/p95/p99、错误率、队列年龄、
scheduler tick 新鲜度、DB 连接饱和、SSE 响应等任何可门控数值；ADR 0030 的 cadence（5s/30s/300s）与
ADR 0031 的备份契约均显式声明「不是 SLO，DG-05 管 SLO」。因此需要一份独立 ADR 固定这些阈值。

### 经核验的部署与运行契约（阈值锚定依据，非本 ADR 引入）

- 部署：单台内网服务器 Docker Compose（ADR 0003/0006/0030），7 服务：PostgreSQL 16、Redis 7（仅
  broker）、FastAPI API（单 uvicorn 进程，`--host 0.0.0.0 --port 3000`，无 `--workers`，async）、
  Celery worker（`--concurrency=2` 默认）、ADR 0030 single-active scheduler、Vue 前端、TLS 反向代理。
- 容量基线（ADR 0009）：≤300 用户 / 5,000 项目 / 每周 1,000 封周报；RPO 24h / RTO 4h。
- DB：异步 psycopg，`pool_pre_ping=True`，默认 async pool（每进程一个 engine：API、worker、scheduler
  各自独立）。PostgreSQL 默认 `max_connections=100`。
- 任务可靠性（ADR 0018）：`CELERY_TASK_TIME_LIMIT=900s`、lease `300s`、heartbeat；状态机
  `QUEUED/RUNNING/RETRY_WAIT/SUCCEEDED/FAILED/CANCELLED`；`UNIQUE(kind,idempotency_key)` 幂等；
  transactional outbox at-least-once；reconcile 恢复 lost dispatch / 到期 retry / expired lease。
- Scheduler（ADR 0030）：single-active PostgreSQL session-level advisory lock；cadence 为 operational
  默认值（outbox drain 5s / reconcile 30s / mailbox sync 300s，env 可调）；liveness 探针
  `:9191/healthz` 仅当（a）持有 advisory lock 且（b）最近成功 tick 在 ≤ 2× outbox drain 窗口（默认
  10s）内才 healthy；per-tick/per-function 失败隔离。
- Health：`GET /api/health` 为静态进程 liveness（`status=ok`）；管理概览（ADR 0023）聚合
  API/DATABASE/REDIS/WORKER/AI_PROVIDER 真实检查。
- SSE（ADR 0016/0019/0028）：PostgreSQL-backed 有序 event facts；10-min token TTL；reconnect
  `after=lastEventId`；代理 `proxy_buffering off`；Agent execution 走 durable task，heartbeat/背压/取消
  以 ADR 0020/0028 为准。

## Decision

### 0. 阈值性质与边界

- 本 ADR 的所有数值均为**操作/发布验收阈值**，不是架构重写要求，不改变已批准功能契约、不放宽
  fail-closed/security/audit 语义、不引入云依赖、不改变单机内网部署目标。
- 任何为满足阈值所需的 production code / index / migration 变更**不在 T038 实施**：T038 只测量与出
  报告，发现不达标返回 `FAIL finding` 给 owning Task（见 §10）。本 ADR 不授权任何 production 写入。
- 阈值锚定上述经核验的部署契约；若部署拓扑/容量基线变更（如多实例 HA、提高容量），本 ADR 阈值
  失效，须重新评估（ADR 0009 已声明当前不承诺多实例 HA）。

### 1. API latency 阈值

纳入门控的 endpoint class（基于 T037 验收的 17 routers / 104 operations 分类）：

| Endpoint class | 说明 | p50 | p95（gate） | p99（report） |
|---|---|---|---|---|
| Fast read | 单实体/分页列表/dashboard/weekly read/audit query（命中索引的读路径） | ≤ 150 ms | **≤ 500 ms** | ≤ 1.0 s |
| Mutation | risk/todo/import commit/confirmation/hold 等事务写 | ≤ 300 ms | **≤ 800 ms** | ≤ 1.5 s |
| Admin overview | `GET /admin/overview`（聚合 5 项 health rollup，ADR 0023） | ≤ 800 ms | **≤ 2.0 s** | ≤ 3.0 s |
| Async dispatch | Excel 上传预览派发（仅入队，不含 worker 解析） | ≤ 300 ms | **≤ 1.0 s** | ≤ 1.5 s |
| Auth | login/session/password（含 argon2） | ≤ 400 ms | **≤ 1.0 s** | ≤ 1.5 s |

- **Gate（硬阻断）= p95**；p50 为目标参考，p99 为报告项（不单独硬阻断，但 p99 超目标值触发
  WARN 并要求 explain/索引证据）。
- 测量窗口：≥ 60s 持续负载；warmup ≥ 30s（样本丢弃）；每个 endpoint class 最少成功样本 ≥ 500
  （p95 门控）/ p99 报告需 ≥ 1000 样本。
- 并发假设：50 个并发认证虚拟用户（≈ 300 用户的 ~17% 同时活跃），混合各 endpoint class。
- internal-server 部署假设：单 uvicorn 进程 + 单 Celery worker（concurrency=2）+ PostgreSQL 16 +
  Redis 7，即 T035 production Compose 拓扑；测量在 production-like Compose 上执行，资源规格
  （CPU/RAM/disk）记录于报告。
- Agent SSE 初始事件延迟单列于 §6，不并入本表。

### 2. Error-rate 阈值

| 指标 | gate（硬阻断） | target（WARN 参考） |
|---|---|---|
| HTTP 5xx 比例（测量窗口内全部 endpoint） | **≤ 0.5%** | ≤ 0.1% |
| 任务执行失败（非基础设施 terminal `FAILED`，排除外部 AI/IMAP 替身已知失败） | **≤ 1%** | ≤ 0.1% |
| 任务触发 ≥1 次 retry 的比例 | **≤ 5%** | ≤ 2% |
| Scheduler tick 失败率（10min 窗口） | **≤ 1%** | 0 |
| SSE 非预期断连率（稳态下，每连接每 5min） | **≤ 1 次** | 0 |
| SSE reconnect/resume 事件正确性（无丢失/无重复） | **100%** | 100% |

- HTTP 5xx 中，由外部 AI Provider / IMAP 替身引起的 5xx 须在报告中分类标注；替身路径的 5xx 不计入
  硬阻断（其真实吞吐验收属 T039 外部 E2E），但须明确记录为替身、不得伪报为真实 PASS。
- 任务 retry 比例反映是否过载而非业务正常重试；正常外部超时重试不计入时应单独说明。

### 3. Worker / queue 可靠性

| 指标 | gate（硬阻断） | 说明 |
|---|---|---|
| Durable task queue age（QUEUED→RUNNING 建队到被 claim 的墙钟） | **p95 ≤ 15s** | 锚定 outbox drain 5s + worker pickup；p99 报告 ≤ 30s |
| Outbox 未发布年龄（committed `TaskOutbox.publishedAt IS NULL` 的最大年龄） | **≤ 30s** | 健康时应 ≤ 2× drain（10s）；超 30s 判 scheduler 停滞 |
| Retry backlog（`RETRY_WAIT` 任务数） | **≤ 50 且非单调增长** | baseline 规模下不得无界堆积 |
| Worker availability | **`inspect ping` ≥ 1 node 回复，100% 测量窗口** | 0 node 即 FAIL |
| Worker heartbeat 新鲜度 | **无任务 lease 超期未 heartbeat（lease 300s）** | expired lease 须被 reconcile 回收 |

### 4. Scheduler 可靠性

| 指标 | gate（硬阻断） | 说明 |
|---|---|---|
| Tick 新鲜度 | **liveness healthy ≥ 99% 测量窗口；连续 unhealthy ≤ 30s** | 锚定 liveness 窗口 ≤ 10s |
| Advisory-lock ownership（single-active） | **100%，无双活** | 第二实例须 5s 内 fail-fast `exit 1` |
| Outbox/reconcile/mailbox cadence 容差 | **任一 action 不停滞超过 2× 其 interval** | drain 5s / reconcile 30s / mailbox 300s；停滞超 2× interval 即 FAIL |
| Per-tick/per-function 失败隔离 | **单 action 失败不阻塞其他 action/循环** | 失败不 advance `_last_run` → 重试 |

### 5. Database 阈值

| 指标 | gate（硬阻断） | 说明 |
|---|---|---|
| 连接饱和 | **峰值 active 连接 ≤ 70% `max_connections`；pool 取连接超时 = 0 次** | 默认 `max_connections=100`；留余量给运维/备份 |
| 事务/锁等待 | **无事务锁等待 > 1s** | `pg_stat_activity` wait event；p95 锁等待 ≤ 200ms（report） |
| Slow-query | **> 2s 的查询 = 0；slow-query（> 500ms）比例 ≤ 1%** | 经 `pg_stat_statements` / `log_min_duration_statement=500ms` 采集；已知长聚合单列说明 |
| 存储/磁盘压力 | **PostgreSQL 磁盘占用 < 80%；`pg_database_size` 增长不超过预期 fixture 规模** | T038 load fixture 为有界数据集，不应显著膨胀存储 |

- DB 阈值不放宽审计 append-only trigger（ADR 0008）或任何 fail-closed 语义；slow-query 优化以索引/explain
  证据形式由 owning Task 落地，T038 只发现不修复。

### 6. SSE / Agent 响应性

| 指标 | gate（硬阻断） | 说明 |
|---|---|---|
| 初始事件延迟（SSE 连接建立到首个事件） | **p95 ≤ 2s** | p99 报告 ≤ 3s |
| Reconnect/resume | **`after=lastEventId` 不重放已见事件、不跳号，100%** | 断连后重连必须可恢复 |
| Heartbeat / keepalive | **≤ 15s 间隔** | 无静默挂起 |
| Provider 失败背压 | **在 task timeout（900s）内必发出 retryable error event；无超 task timeout 的静默挂起** | 替身 Provider 失败须被分类记录 |

- SSE 响应性在替身 Provider 下测量；真实 Provider 吞吐属 T039 外部 E2E，不在 T038 PASS 范围。

### 7. 备份/恢复操作阈值

- T038 不显式消费备份/恢复作为性能门（T038 stop conditions 与 objective 均不含备份）；备份/恢复语义
  归 ADR 0031（T036）/ T039 演练。本 ADR **不重开 ADR 0031 语义**，不新增备份数值门。
- 唯一关联：T038 容量负载不得违反 ADR 0009 的 RPO（24h）/ RTO（4h）基线——即负载下日常备份窗口与
  恢复演练仍可在既定 RPO/RTO 内完成；此为既有 ADR 0009 基线的 reaffirm，非新门。

### 8. Release gate 语义

- **PASS**：全部硬阻断（gate）阈值在可重复条件下连续 2 次运行均满足，且必需证据完整。
- **WARN**：target（参考）阈值超标但硬阻断满足；记录于报告，非阻断。
- **FAIL**：任一硬阻断阈值被违反，或必需证据缺失/不完整，或测量不可重复到可接受方差内。
- 硬阻断清单（FAIL 触发）：API p95 latency（§1）、HTTP 5xx 比例（§2）、任务失败率（§2）、scheduler
  tick 新鲜度/single-active/cadence 容差（§4）、worker availability（§3）、DB 连接饱和/slow-query
  （§5）、SSE resume 正确性/初始延迟/heartbeat（§6）、outbox 未发布年龄/retry backlog（§3）。
- Flaky/瞬态样本重试：单个样本超阈值时，对该 scenario 最多再跑 2 次；3 次中 2 次通过视为瞬态
  （WARN + 记录）；3 次中 2 次失败判 FAIL。环境性失败（容器崩溃/infra 不可用）→ 停止，该 scenario
  标 `UNVERIFIED`，**不得记为 PASS**。
- **禁止「best-effort PASS」**：当某维度必需证据缺失（如 T038 用替身 AI/IMAP，无法给出真实外部吞吐）
  时，该维度记为「substituted-path evidence」并显式标注替身，**不得记为 PASS**；真实外部 E2E 推迟至
  T039。T038 的 PASS 范围限定为不依赖外部 AI/IMAP 即可度量的维度（API 延迟/错误率/worker/queue/
  scheduler/DB/SSE 响应性 under 替身）。

### 9. 测量方法学

- **数据集**：可复现 generator 产出 ADR 0009 baseline（300 用户 / 5,000 项目 / 1,000 周报），并 seed
  到真实规模的 risks/todos/audit/timeline/conversations/mailbox handoff 记录。
- **并发**：50 并发认证虚拟用户（API latency）；任务负载 = 按基线速率持续 enqueue 混合 task kind。
- **参考环境**：本地单机 = T035 production Compose（7 服务，同一 image/env），非云；资源规格
  （CPU/RAM/disk class）记录于报告。
- **可重复性**：PASS 需连续 2 次运行在可接受方差内——p95 延迟 run 间 ±20%，error-rate delta ≤ 0.3pp。
- **替身**：仅使用已批准 test double（T037 的 3 类：`current_identity` override 不适用于 load；
  `_StaticResolver` 出站解析；`_CeleryRecorder`）。AI Provider 以有界确定性 fake 替身返回合法 V1/V2
  输出；IMAP 以本地 mailbox fixture 替身。所有替身在报告中显式记录。
- **采集**：延迟经请求级计时（含 `/api` 经代理的端到端，分离直连 vs 代理）；DB 指标经
  `pg_stat_statements` / `pg_stat_activity` / `pg_database_size`；scheduler 经 `:9191/healthz` 快照
  与 tick 日志；worker 经 `inspect ping` 与 `DurableTask` 状态时序。

### 10. 归属（ownership）

- **T038 直接拥有** benchmark/load-test 脚本、generator、scenarios、原始结果与 PASS/FAIL 报告
  （write-set `apps/api-python/tests/load/**` + `artifacts/load/**`）。T038 直接实施，**无需新增
  remediation Task**——T038 spec 已将「Generator, scenarios, raw summary, PASS/FAIL report」列为
  required deliverables。
- 任何为达标所需的 production code / index / migration 变更 = T038 的 `FAIL finding`，返回 owning Task
  实施（例如：dashboard/weekly 查询优化 → T020/T027；reliability → T008；scheduler → T046；DB 索引 →
  对应 schema-owning Task）。T038 **不实施任何 production 修复**。
- T038 readiness：本 ADR 批准后 DG-05 阻断解除；T037 dependency 已满足（T037 `REVIEW_PASSED` +
  Wave 24 Integration `PASS`）。T038 由 `BLOCKED_DESIGN_GAP (DG-05)` 恢复为 `READY`，可在 Wave 25
  显式授权后执行。

## Consequences

- 正向：T038 获得可门控的数值阈值与发布门语义，DG-05 解除，Wave 25/T038 进入 `READY`；阈值锚定
  已核验部署契约，可独立 Agent 在 production-like Compose 上重复测量。
- 正向：明确「测量 vs 修复」边界——T038 只出证据，production 修复归 owning Task，避免越界写
  frozen write-set。
- 约束：真实外部 AI/IMAP 吞吐不在 T038 PASS 范围（推迟 T039），T038 不得以替身伪报真实外部 PASS。
- 约束：本 ADR 阈值依赖单机内网部署与 ADR 0009 容量基线；拓扑/容量变更则阈值失效需重评。
- 不变：ADR 0009 基线（300/5000/1000、RPO 24h/RTO 4h、7/4/12 保留）不变；ADR 0031 备份语义不重开；
  fail-closed/security/audit 语义不放宽；无云依赖；无 production code 变更。
- 不变：ADR 0030 cadence 仍为 operational 默认值，本 ADR 不将其升级为 SLO——本 ADR 的是 cadence
  容差门（停滞超 2× interval 即 FAIL），而非把 5s/30s/300s 本身变为性能阈值。
