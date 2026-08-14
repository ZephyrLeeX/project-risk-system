# Wave 21 — Partial（T046 production scheduler entrypoint）

- **Wave:** 21
- **Tasks:** T046（单一 work unit；ADR 0030 / DG-16 resolution 引入）
- **状态:** `IN_PROGRESS`（partial — T046 `REVIEW_PASSED` + code checkpoint；Wave 21 Integration 未启动）
- **Wave 21 Integration:** 未启动
- **下一 Wave / DG-05 / DG-08:** 未启动 / 未处理

## 背景

Wave 21 原为 T035（production Compose/proxy/images）。T035 执行 stop 于 `DESIGN_GAP`（DG-16）：批准的架构无 scheduler 组件，三个需周期驱动的入口（`publish_outbox`/`reconcile`/`schedule_enabled_syncs`）均无 production caller，materialize scheduler 超出 T035 Compose/proxy/Dockerfile/env/docs write-set 且需未经批准决策。

ADR 0030 解决 DG-16：批准 production 调度器拓扑为**独立 scheduler 进程**（不采用 Celery Beat），以 PostgreSQL session-level advisory lock 保证 single-active，周期驱动三入口。**scheduler application entrypoint 归属新增 remediation Task T046**（write-set 限 `scheduler.py` + tests，只读复用五个 public 入口）；T035 仅拥有 Compose `scheduler` service wiring，**T035 deps 增 +T046**。DAG 新增 `T008 & T024 --> T046 --> T035`；Wave 表插入 Wave 21 = T046，T035 后移至 Wave 22。

DG-16 resolution 设计 checkpoint：`67f1ddd01ec60e96dc7442549d6647e1fd9d6d89`（未实施 production code、未修改 `infra/docker-compose.yml`、未执行 T035/T046、未启动 Wave 21 Integration）。

## Readiness

- T046 blocking 前置 T008（`REVIEW_PASSED`）、T024（`REVIEW_PASSED`）已满足；ADR 0030 提供批准的 cadence/single-active/failure/outbox-wiring 契约。无新 blocker → Wave 21 标记 `IN_PROGRESS`，仅授权 T046。
- Lean Execution Mode 加载：T046 Task、ADR 0030、ADR 0018、T024 contract、`publish_outbox`/`reconcile`/`schedule_enabled_syncs`、`risk_platform.db`、shared `celery_app`、必要 tests。

## T046 结果：`REVIEW_PASSED`

新增 `apps/api-python/src/risk_platform/scheduler.py`（进程入口、advisory-lock tick 循环、per-function cadence gating、per-tick/per-function failure isolation、liveness 探针、startup retry、SIGTERM graceful shutdown）+ `tests/reliability/test_scheduler.py`（21 tests：15 unit + 6 PostgreSQL 16/Redis 7）。

- **single-active**：`pg_try_advisory_lock` 固定单 bigint key `0x7269736B5F736368`；`False` → exit 1 fail-fast；session-level lock 随连接存活、崩溃即释放。
- **cadence**（operational defaults，非 SLO）：outbox drain 5s、reconcile 30s、mailbox sync 300s，env 可调，非正 → exit 2。
- **failure isolation**：每 action 独立 try/except，失败不 advance `_last_run` → 重试，不阻塞其他动作/循环。
- **outbox at-least-once**：`publish_outbox` 先 `send_task` 后置 `publishedAt`，事务回滚保持未发布。
- **no dual-write / 唯一 publisher**：请求路径不 `send_task`，scheduler 是唯一 publisher；只读复用五入口，未编辑 frozen `composition.py`/`celery_app.py`/`worker.py`/`main.py`/driven 函数。
- **liveness**：`/healthz` 仅当 lock 持有 + 最近 tick 在 ≤ 2× outbox drain 窗口内时 200，否则 503；stdlib `ThreadingHTTPServer` 无额外依赖。
- **shutdown**：SIGTERM 完成当前 tick 后释放 lock + 关连接，exit 0；config error exit 2；DB 有界重试超限 exit 1。

Independent Review `REVIEW_PASSED`，无 blocking finding（3 个 minor non-blocking note）。详见 `docs/implementation/reports/T046.md`。

## Validation（全部 PASS）

- Ruff：`All checks passed!`
- mypy：`Success: no issues found in 187 source files`
- focused pytest（PostgreSQL 16 + Redis 7）：`21 passed`
- full backend regression（PostgreSQL 16 + Redis 7）：`304 passed, 1 skipped`（较 Wave 19 `283` 增 21）
- `uv lock --check`：`Resolved 58 packages`
- `git diff --check`：clean
- write-set 合规：仅 `scheduler.py` + `test_scheduler.py`；`infra/docker-compose.yml` 与全部 frozen write-set 未修改

## code checkpoint

T046 code checkpoint `21c6d3d0b1be32034fb55bf17e02501506d62ccc`（metadata 由后续提交补录于 `EXECUTION_STATE.md` / `T046.md`）。

## T035 readiness 同步（仅 metadata，未执行 T035）

T046 `REVIEW_PASSED` + code checkpoint 解除 T035 的 blocked-on-T046。T035（deps T031-T034/T040/**T046**）状态保持 `READY`，可在 Wave 22 重新评估/执行；T035 拥有 Compose `scheduler` service wiring（image/command/env/healthcheck/restart/depends_on）+ env examples + deployment docs，不实现 entrypoint。本次不执行 T035、不修改 `infra/docker-compose.yml`。

## 未执行项（按本次授权与协议边界）

- Wave 21 Integration：**未启动**。
- Wave 22 / T035：**未启动**（仅同步 readiness）。
- DG-05（performance/reliability numeric thresholds）、DG-08（backup encryption/key/consistency）：**未处理**。
- frozen write-sets（T040 `celery_app.py`/`composition.py`/`worker.py`/`main.py`、T008 reliability、T024 mailbox sync、三个 driven 函数）与 frozen OpenAPI authority：**未修改**。
- `infra/docker-compose.yml`（T035 独占 write-set）：**未修改**。
