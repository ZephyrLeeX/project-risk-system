# Wave 21 Integration Report

## 结果

- Wave 21 Integration：`PASS`
- T046（production scheduler entrypoint）：`REVIEW_PASSED`（code checkpoint `21c6d3d0b1be32034fb55bf17e02501506d62ccc`；metadata checkpoint `4ef3f18`）
- integration fix：无（T046 write-set 未暴露任何 integration failure）
- 下一 Wave（Wave 22 = T035）：未启动（仅同步 readiness）
- DG-05 / DG-08：未处理
- `infra/docker-compose.yml`（T035 独占 write-set）：未修改
- frozen write-set（`composition.py`/`celery_app.py`/`worker.py`/`main.py`、T008 reliability、T024 mailbox sync、三个 driven 函数、frozen OpenAPI authority）：未修改

## 范围

Wave 21 为单工作单元 Wave，授权 T046（ADR 0030 / DG-16 resolution 引入的 production scheduler entrypoint）。
T046 已在其执行单元完成 `REVIEW_PASSED` + code checkpoint。本 Integration 在 T046 `REVIEW_PASSED` 之后执行
项目级联合验证，重点验证 T046 scheduler 与既有 durable-task / mailbox / reliability 体系的联合行为，确认
独立 scheduler 进程在真实 PostgreSQL 16 + Redis 7 + real Celery worker 环境下满足 ADR 0030 契约，且未
引入回退或越界修改。

T046 write-set 仅为 `apps/api-python/src/risk_platform/scheduler.py`（新）+ `tests/reliability/test_scheduler.py`；
只读复用五个 public 入口（`risk_platform.db` session factory、`celery_app`、`publish_outbox`、`reconcile`、
`schedule_enabled_syncs`），未编辑任何 frozen write-set。本 Integration 未修改任何 production 代码、测试基线、
migration、contract artifact 或 `infra/docker-compose.yml`。

## 契约联合一致验证（15 项全部通过）

### 1. 独立 scheduler entrypoint 可在真实 PostgreSQL 16 + Redis 7 启动

real-process smoke：以 `risk_platform.scheduler:main` 启动真实进程（`DATABASE_URL` 指向真实 PostgreSQL 16.14
`risk_test`，`CELERY_BROKER_URL` 指向真实 Redis 7.4.10），进程获取 advisory lock、进入 tick 循环、liveness
探针监听并响应。`async_main` 构造 production 依赖（engine/session_factory/lock_conn/cadence/liveness/server），
`Scheduler.run` 在真实 PG 上完成 tick。

### 2. PostgreSQL session-level advisory lock（single-active / fail-fast / 重获取）

- real-process：进程 A 持有 lock → 进程 B 启动 fail-fast `exit 1`（lock 已被持有）→ A 仍存活 → SIGTERM A
  释放 lock → 进程 C 重新获取已释放的 lock。三者均经真实 PG `pg_try_advisory_lock`/`pg_advisory_unlock`
  固定单 bigint key `0x7269736B5F736368`。
- focused test `test_single_active_advisory_lock_rejects_then_reacquires`（两条真实 PG 连接：第二条被拒、
  第一条释放后第二条重获取）+ `test_acquire_lock_returns_false_immediately_when_held`（fail-fast 不重试）+
  unit `test_second_instance_is_rejected_without_releasing`（被拒时不调用 release）。

### 3. cadence（outbox drain 5s / reconcile 30s / mailbox sync 300s + env override）

- `test_cadence_defaults_match_adr_0030`：defaults 5s/30s/300s，liveness window 10s（2× outbox drain）。
- `test_cadence_from_env_overrides`：env override 全部生效。
- `test_cadence_from_env_rejects_non_positive`：非正/非法值 → `SchedulerConfigurationError`（`exit 2`）。

### 4. 三类 action 独立调度，单项 failure 不阻塞其他 action

- `test_cadence_scheduling_drives_each_function_at_its_own_interval`：outbox/reconcile/mailbox 各按自身
  interval 触发（5s/10s/15s 模拟下分别为 7/4/3 次）。
- `test_independent_failure_isolation_and_retry`：某 action 抛异常时其余 action 仍执行，失败 action 的
  `_last_run` 不前进 → 下一 tick 重试，不阻塞循环或其他 action。
- real-process：risk_test public schema 无业务表时三个 action 每 tick 报错并被隔离，进程持续 tick、
  liveness 保持 200（`record_tick` 在 action 循环之后无条件执行）。

### 5. `publish_outbox`（committed → broker / send 成功后才标记 published / send 失败保持未发布 / 无 dual-write）

- `test_outbox_drain_retries_after_send_failure_then_publishes`：broker `send_task` 失败时 row 保持
  `publishedAt=None`（事务回滚），下一 tick 重试成功后才置 `publishedAt`。
- `test_scheduler_reuses_publish_outbox_without_inline_send_task`：scheduler.py 复用 `publish_outbox`，
  无内联 `send_task`（无重复 publisher）。
- `test_publish_outbox_has_no_request_path_or_worker_caller`：`publish_outbox` 的 production caller 仅
  `scheduler.py`（定义于 `dispatcher.py`）；`worker.py`/`main.py`/`composition.py` 不引用 → 请求路径不
  `send_task`，PostgreSQL 唯一 authority，Redis/Celery 仅 delivery transport，无 DB/Celery dual-write。

### 6. `reconcile` 可恢复 broker-loss / missed-delivery 场景

- `test_reconcile_action_runs_against_postgresql`：空 DB 上 `reconcile` 返回 0、无错误、无 audit 写入。
- `reconcile` 为既有 T008/T018 reliability core 入口（fenced lease 回收 / missed-delivery 重投递），其
  broker-loss 恢复语义由 reliability 全量回归（full pytest `test_dispatcher.py` 等）覆盖；scheduler 仅以
  caller-owned transaction 周期驱动，不修改 `reconcile`。

### 7. `schedule_enabled_syncs` 产生 scheduled mailbox work + idempotency

- `test_mailbox_schedule_action_runs_against_postgresql`：无启用 mailbox → 无 batch、无错误。
- mailbox sync idempotency（fresh-key `enqueue_task`、不产生重复任务）由 `tests/mailbox/test_sync.py`
  （2 passed）覆盖；scheduler 仅周期驱动，不修改 `schedule_enabled_syncs`。

### 8. real Celery worker 能消费 scheduler 发布的 durable task

- 端到端 ad-hoc 验证（独立 per-test schema + Alembic `upgrade head`/`check`，isolated Redis DB 14，
  dedicated queue，real `start_worker(pool="solo")`）：scheduler `make_drain_outbox` → `publish_outbox`
  → `celery.send_task("risk_platform.reliability.execute", [task_id, gen])` → 真实 Redis 7 broker → real
  solo worker → `execute_message` → fenced `claim_task` → handler → `finish_task(success=True)` → DB
  `DurableTask.status=SUCCEEDED` + `TaskOutbox.publishedAt` 已置。结果 `INTEGRATION_OK`。
- 既有 `test_outbox_drain_publishes_to_real_redis_broker`：drain tick 的 `send_task` 到达真实 Redis 7
  broker（DB 15，`llen("celery")==1`），`publishedAt` 已置。
- 既有 T029/T026 real-worker acceptance（full pytest 内）：real solo worker 消费
  `risk_platform.reliability.execute` 完成 fenced claim → handler → DB 状态转移。

### 9. SIGTERM graceful shutdown（当前 tick 完成 / 连接关闭 / advisory lock 释放）

- real-process：SIGTERM 进程 A → `exit 0`（当前 tick 完成后释放 lock + 关连接）；进程 C 同样 `exit 0`。
- `test_graceful_shutdown_completes_current_tick_and_releases`：tick 中 `stop.set()` 后当前 tick 仍跑完
  （`a1/a2/a3` 全部执行），随后 release 被调用，`exit 0`。
- `test_lock_connection_loss_stops_the_loop`：lock 连接不可用 → `exit 1`（触发重启），release 仍调用。

### 10. liveness（lock 持有 + recent tick 在窗口内 / stale / lock-lost 失败）

- real-process：进程 A/C 运行中 `/healthz` 返回 `200`。
- `test_liveness_state_reflects_lock_and_recent_tick`：lock 未持有 / 无 tick / tick stale / lock 丢失 →
  `healthy=False`。
- `test_liveness_http_probe_reports_200_then_503`：healthy → 200；stale tick → 503；lock lost → 503。
- `test_scheduler_run_loop_against_postgresql`：run 期间 `healthy_during_run` 全部为 True（lock 持有 +
  recent tick），stdlib `ThreadingHTTPServer` 无额外依赖。

### 11. scheduler 不写业务 audit

`scheduler.py` 不 import / 不调用任何 audit service；`test_reconcile_action_runs_against_postgresql`
注释明确 reconcile 空跑无 audit 写入。ADR 0017：业务效果由 owning domain path 审计，scheduler 仅
metadata-only 驱动。

### 12. 日志不泄漏 secret / mail content / payload

`grep -niE "audit|secret|password|payload|beat_schedule|send_task" scheduler.py` 仅命中 docstring 中
"writes no audit" / "no secret, mail content or task payload" 声明；runtime 日志仅结构化字段（action name、
attempt 计数、lock 状态、连接错误），不含 task payload / mail content / secret（ADR 0014/0007）。

### 13. 不存在 Celery Beat wiring

`scheduler.py` 无 `beat_schedule`、无 Beat 配置；`celery_app.py`（frozen T040）`grep beat` 无命中。ADR 0030
明确不采用 Celery Beat，scheduler 为独立进程。

### 14. frozen `main.py` / `worker.py` / `composition.py` / `celery_app.py` 保持不变

`git diff --stat 21c6d3d HEAD -- main.py worker.py composition.py celeryery_app.py`（含 `celery_app.py`）为空；
working tree clean。四个 frozen write-set 自 T046 code checkpoint 起未修改。

### 15. `infra/docker-compose.yml` 保持未修改

`git diff --stat 21c6d3d HEAD -- infra/docker-compose.yml` 为空；working tree clean。本 Wave 未做 T035
Compose wiring。

## Validation（全部 PASS）

| 检查 | 结果 |
| --- | --- |
| Ruff | `All checks passed!` |
| mypy | `Success: no issues found in 187 source files` |
| focused scheduler tests（PostgreSQL 16 + Redis 7 + real broker） | `21 passed` |
| focused reliability + mailbox tests（PostgreSQL 16 + Redis 7） | `74 passed` |
| full backend pytest（PostgreSQL 16 + Redis 7） | `304 passed, 1 skipped`（与 T046 checkpoint 一致；1 skip 为既有 audit-query-export DB 专项分工提示） |
| real Celery worker 端到端（scheduler publish → real solo worker → task SUCCEEDED） | `INTEGRATION_OK` |
| real-process single-active smoke（fail-fast `exit 1` / SIGTERM `exit 0` / 重获取 / liveness 200） | `SMOKE_OK` |
| `uv lock --check` | `Resolved 58 packages` |
| `git diff --check` | clean |
| Alembic head | `20260812_0008`（per-test schema `upgrade head` + `alembic check` 无新 migration） |

环境：PostgreSQL 16.14（`postgres:16-alpine`，`project-risk-postgres`）+ Redis 7.4.10（`redis:7-alpine`，
`project-risk-redis`）+ real Celery `solo` worker；`TEST_DATABASE_URL` 指向 `risk_test`，per-test schema
fixture 隔离（`t046_<uuid>` / `w21int_<uuid>`）。

## integration fix

无。T046 write-set（`scheduler.py` + `test_scheduler.py`）在真实 PostgreSQL 16 + Redis 7 + real Celery worker
联合环境下未暴露任何 integration failure。本轮未修改任何 production 代码、测试基线、migration、contract
artifact、frozen write-set 或 `infra/docker-compose.yml`。

## T035 / Wave 22 readiness 同步（仅 metadata，未执行 T035）

T046 `REVIEW_PASSED` + code checkpoint + Wave 21 Integration `PASS` 后，T035（deps T031-T034/T040/**T046**）
保持 `READY`，可在 Wave 22 重新评估/执行。T035 拥有 Compose `scheduler` service wiring
（image/command/env/healthcheck/restart/depends_on）+ env examples + deployment docs，不实现 entrypoint
（entrypoint 已由 T046 交付 `risk_platform.scheduler:main`）。本次不执行 T035、不修改
`infra/docker-compose.yml`、不启动 Wave 22。

## 未执行项（按本次授权与协议边界）

- Wave 22 / T035：未启动（仅同步 readiness）。
- DG-05（performance/reliability numeric thresholds）、DG-08（backup encryption/key/consistency）：未处理。
- frozen write-sets（T040 `celery_app.py`/`composition.py`/`worker.py`/`main.py`、T008 reliability、T024
  mailbox sync、三个 driven 函数）与 frozen OpenAPI authority：未修改。
- `infra/docker-compose.yml`（T035 独占 write-set）：未修改。

## Final checkpoint

Wave 21 final checkpoint：`3722a6b94fa4692d1452c6c34eb072be0fa4382f`（metadata 记录于 `EXECUTION_STATE.md`）。
