# 0030 — 定义生产调度器与 outbox 投递拓扑

## Status

Accepted（解决 DG-16）。本 ADR 明确 production 周期触发机制、scheduler 进程归属与 write-set、cadence、single-active、failure 语义与 ADR 0018 outbox 的 production 投递接线，不修改 ADR 0018 的 durable-task 契约，仅澄清其 publisher 的触发方式。

## Context

ADR 0006 批准的架构为模块化 FastAPI 单体 + 独立 Celery Worker + Redis broker + PostgreSQL；设计 §4 架构图含 `Web → Proxy → API → PostgreSQL/Redis → Worker`，**未含 scheduler/beat 框**。ADR 0018 要求 transactional outbox publisher（"outbox publisher 在数据库 transaction 提交后向 Celery 发布"）与 Reconciler（lost dispatch / retry due / expired lease），但**未规定其触发机制、cadence 或进程归属**。T024 的 scheduled mailbox sync（`schedule_enabled_syncs`，`autoSyncIntervalMinutes`/`nextSyncAt`，默认 `_INTERVAL_MINUTES=30`）也需周期触发。

DG-16（T035 执行 stop）确认：`publish_outbox`、`reconcile`、`schedule_enabled_syncs` 三个 service entry point 均无 production caller；无 `*scheduler*`/`*beat*` entrypoint 模块；`celery_app.py`（frozen T040 write-set）无 `beat_schedule`；`enqueue_task` 在 caller 事务内写入 `DurableTask` + `TaskOutbox` 后，production 路径从不调用 publisher，任务被持久化但不投递到 Celery。T035 的 Objective 要求 production Compose 含 "scheduler" 组件，但无已批准机制可在 T035 的 Compose/proxy/Dockerfile/env write-set 内 materialize，且任何 scheduler entrypoint 需新增 application 代码并可能跨越 T040 frozen composition ownership。

本 ADR 在不实施、不修改 production code 的前提下，批准生产调度器拓扑并界定归属，使 T035 可在后续被解锁。

## Decision

### 1. 拓扑：独立 scheduler 进程（不采用 Celery Beat）

production 单机 Compose 新增**一个独立 Python 进程 `scheduler`**，与 `api`、`worker` 并列，复用同一镜像。scheduler 是**唯一的周期触发 owner**，周期驱动三个已存在的 service entry point：

| 周期动作 | 调用入口 | 来源契约 |
|---|---|---|
| outbox drain（投递 committed `task_outbox` 到 Celery） | `publish_outbox(session, celery_app, *, limit)` | ADR 0018 §"Transactional outbox 与投递" |
| reconciliation（lost dispatch / retry due / expired lease） | `reconcile(session, *, now, limit)` | ADR 0018 §"Reconciliation" |
| scheduled mailbox sync（为到期 mailbox 创建 SCHEDULED 批次） | `schedule_enabled_syncs(session_factory)` | T024 / ADR 0022 |

**不采用 Celery Beat**：beat 需在 frozen `celery_app.py` 增加 `beat_schedule`（跨 T040 write-set）且需 schedule 持久化存储（额外状态与运维面）；独立 scheduler 进程以最小依赖复用 `risk_platform.db` 与 shared `celery_app`（read-only import，仅用于 `send_task` 投递），不注册 executor、不配置 beat、不构造 domain service，**不触碰 `composition.py`/`celery_app.py`/`worker.py`/`main.py`**，从而不跨越 T040 composition ownership。这与 `worker.py` 既有的最小直接构造模式一致（`create_session_factory(create_database_engine(database_url()))`）。

进程关系：`postgres`、`redis` healthy → `scheduler`、`worker`、`api` → `proxy` → `web`。scheduler 不依赖 `worker` 或 `api` 在线即可运行：它只向 Redis/Celery `send_task`，由 `worker` 异步消费；`worker` 离线时消息在 broker 等待，PostgreSQL 仍为唯一事实源。

### 2. scheduler entrypoint 归属与 write-set

scheduler application entrypoint 属于**新增 remediation Task T046**（非 T035）。T046 write-set 限定为：

- 新增 `apps/api-python/src/risk_platform/scheduler.py`（进程入口、advisory-lock 循环、tick 编排、liveness 探针）；
- 对应 tests。

T046 **只读复用**（不修改）：`risk_platform.db`（`database_url`/`create_database_engine`/`create_session_factory`）、`risk_platform.reliability.celery_app.celery_app`、`risk_platform.reliability.dispatcher.publish_outbox`、`risk_platform.reliability.core.reconcile`、`risk_platform.mailbox.sync.schedule_enabled_syncs`。

**禁止**编辑 `composition.py`、`celery_app.py`、`worker.py`、`main.py` 或三个 driven 函数。若实现中发现 driven 函数签名需要 composition-owned 依赖（cipher/provider/tool-registry），T046 触发 stop condition（DESIGN_DEVIATION）并停止——但本 ADR 已确认三个入口仅需 session/session_factory/celery_app，scheduler 不需要 cipher（`schedule_enabled_syncs` 仅创建批次，不解密 mailbox 凭据；解密由 sync worker 执行批次时完成）。

T035 仅拥有 scheduler 的 **deployment wiring**（Compose `scheduler` service、env examples、deployment docs），属其 Compose/proxy/Dockerfile/env/docs write-set；T035 不拥有 entrypoint。因此 **T035 依赖 T046**。

### 3. Cadence（operational defaults，非 SLO）

以下为批准的 operational 默认值，可经 env 调整，**不是性能验收阈值**（DG-05 管 SLO，本 ADR 不处理）：

| 动作 | 默认间隔 | env |
|---|---|---|
| outbox drain | 5s | `SCHEDULER_OUTBOX_DRAIN_INTERVAL_SECONDS` |
| reconciliation | 30s | `SCHEDULER_RECONCILE_INTERVAL_SECONDS` |
| scheduled mailbox sync | 300s（5 min） | `SCHEDULER_MAILBOX_SYNC_INTERVAL_SECONDS` |

每个 tick 内三动作按各自间隔独立判断是否到期，互不阻塞（见 §5）。outbox drain 间隔决定任务投递延迟上界（默认 ≤5s）；mailbox sync 5 min 轮询足以在 `nextSyncAt` 到期后一个轮询窗口内创建批次（mailbox 自身同步周期为 30 min）。

### 4. Single-active 语义

scheduler 启动时在专用长连接上获取 **PostgreSQL session-level advisory lock**（`pg_try_advisory_lock(<固定 key>)`）：

- 获取成功 → 进入 tick 循环；lock 随该连接存活，进程崩溃/重启时连接关闭自动释放 lock。
- 获取失败（lock 已被持有）→ 进程以非零状态退出（不运行第二个 active scheduler）；Compose `restart: unless-stopped` 会在持有者释放后重启接管。
- 重启重叠 / 误扩缩容 / 双实例场景：第二个实例无法获得 lock，安全退出，不产生重复 active scheduler。

advisory lock 是首选 fence；三个 driven 函数本身亦幂等（publish_outbox 仅在 `send_task` 返回后置 `publishedAt`；reconcile 用行锁/条件更新/`SKIP LOCKED`；schedule_enabled_syncs 受 `UNIQUE(kind, idempotency_key)` 约束），构成 defense-in-depth，满足 ADR 0018 §"Reconciliation"「多个 reconciler 或 publisher 并发运行时不得为同一 task/generation 创建重复 outbox」。session-level（非 transaction-level）lock 确保整个进程生命周期持有，且崩溃即释放，无需额外 fencing token。

### 5. Failure 语义

- **per-tick、per-function 隔离**：每 tick 内 outbox drain / reconcile / schedule_enabled_syncs 各自 `try/except`；一个动作抛错不影响其他动作，亦不中断循环。
- **outbox drain row-level**：`publish_outbox` 逐行 `send_task`，仅成功后才置 `publishedAt`；broker publish 抛错时该行保持未发布，下个 drain 重试（at-least-once，ADR 0018）。已 commit 的 `task_outbox` 永不丢失，仅靠 PostgreSQL 重建（ADR 0018 §"Transactional outbox"）。
- **reconcile / schedule 幂等**：重复 tick 安全。
- **audit 边界**：scheduler 是 reliability/transport 基础设施，**不是业务 actor**；scheduler 自身**不写 audit**（ADR 0017）。业务效果（如 scheduled mailbox sync 批次的执行、风险写入）由各自 owning domain path 按 ADR 0017 metadata-only audit 记录，scheduler 仅触发。
- **日志边界**：仅结构化日志，不含 secret、邮件正文/附件、task payload、prompt 或模型原始响应（ADR 0014/0007）。
- **进程级**：tick 内异常被捕获并记日志后继续；进程仅在启动配置错误或 DB 不可达超过有界重试后崩溃，触发 Compose `restart: unless-stopped`。advisory lock 在崩溃时自动释放，重启可重新获取。

### 6. ADR 0018 production outbox wiring（消除 dual-write）

本 ADR 澄清 ADR 0018「outbox publisher 在数据库 transaction 提交后向 Celery 发布」的 production 触发方式：

- **请求路径不直接调用 Celery**（不 `celery.send_task`，不 post-commit inline dual-write）。`enqueue_task` 在 caller 事务内仅写 PostgreSQL（`DurableTask` + `TaskOutbox`），不触碰 broker——请求路径保持单一写入 PostgreSQL，**无 DB/Celery dual-write**。
- **scheduler 的 outbox drain tick 是唯一 publisher**：周期读取未发布 `task_outbox` 行并 `send_task`。"after commit" 保证由 MVCC 结构性保证——drain tick 只能读到已 commit（对其他事务可见）的 outbox 行。
- **最终投递保证**：committed `task_outbox` 由 drain 在 ≤`SCHEDULER_OUTBOX_DRAIN_INTERVAL_SECONDS` 内投递；broker 全丢后由 reconcile 的 lost-discovery 重建 dispatch generation/outbox（ADR 0018），再由 drain 投递。PostgreSQL 始终为唯一 authority，Redis/Celery 仅为 delivery/execution transport。

publish 调用方 = scheduler（T046 owns）；请求路径与 worker 均不调用 `publish_outbox`。

### 7. Startup / shutdown / health / restart

- **startup**：连接 PostgreSQL → 获取 advisory lock（失败则 fail-fast 退出）→ 进入 tick 循环。
- **shutdown（SIGTERM）**：停止接受新 tick，完成当前 tick，释放 lock（关闭 lock 连接），退出。Compose `stop_grace_period` ≥ 单 tick 最长耗时。
- **healthcheck**：scheduler 进程暴露 liveness 探针，仅当（a）持有 advisory lock 且（b）最近一次成功 tick 在有界窗口内（默认 ≤ 2× outbox drain 间隔）时返回 healthy；否则 unhealthy。探针机制（最小内置 TCP/HTTP 端点，无额外依赖）由 T046 实现。
- **restart**：`restart: unless-stopped`；`depends_on: postgres(healthy), redis(healthy)`；不与 `api`/`worker` 互依赖。scheduler 无状态、无持久卷（lock 与 outbox 均在 PostgreSQL）。

### 8. T035 / T046 归属与依赖

- **T046**（新增 remediation Task，`READY`，deps T008/T024）：实现 scheduler application entrypoint + tests，write-set 限定为 `scheduler.py` + tests，只读复用五个 public 入口。
- **T035**（deps 增 +T046）：在 T046 `REVIEW_PASSED` 后，T035 在其 Compose write-set 内增加 `scheduler` service（image/command/env/healthcheck/restart/depends_on）、env examples 与 deployment docs，不实现 entrypoint。
- DAG 新增 `T008 & T024 --> T046 --> T035`。Wave 表插入 Wave 21 = T046，T035 后移至 Wave 22，其后各 Wave 顺延 +1（T036→23、T037→24、T038→25、T039→26）。

## Consequences

- production 拓扑新增一个无状态 scheduler 进程；设计 §4 架构图应补充 `Scheduler → PostgreSQL (advisory lock + outbox/reconcile/schedule) / Redis-Celery (send_task)` 节点。
- ADR 0018 的 publisher 触发方式被澄清为 scheduler outbox-drain tick，请求路径保持 PostgreSQL 单写、无 dual-write；PostgreSQL 仍为唯一事实源。
- scheduler 不写 audit、不持业务状态、不跨 T040 composition ownership；T046 write-set 与 frozen write-sets 不交叠。
- single-active 由 PostgreSQL advisory lock 保证，无需 beat schedule 存储或额外 fencing 基础设施。
- T035 解除 DG-16 后状态由 `DESIGN_GAP` 恢复为 `READY`（仅 metadata，未实施），并新增对 T046 的依赖；T046 实施与 T035 实施均不在本 ADR 范围内。
- DG-05（numeric SLO）/ DG-08（backup）不受本 ADR 影响，保持 out of scope。
