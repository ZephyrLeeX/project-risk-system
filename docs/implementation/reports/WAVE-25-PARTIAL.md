# Wave 25 — Partial（T038 capacity & resilience load acceptance）

- **Wave:** 25
- **Tasks:** T038（单一 work unit）
- **状态:** `IN_PROGRESS`（partial — T038 `REVIEW_FAILED`；harness + raw evidence 已交付；Wave 25 Integration 未启动；capacity/resilience production 缺陷返回 owning Task；T038 主因 `/api/todos` 分页已由 T047 remediate，`REVIEW_PASSED`，首版低并发可用；其余 T038 findings DEFERRED 至 post-MVP hardening）
- **Wave 25 Integration:** 未启动
- **下一 Wave（Wave 26 / T039）：** 未启动

## 背景

T038（deps T037 和 DG-05）的全部阻断已解除——T037 `REVIEW_PASSED` + Wave 24 Integration `PASS`，DG-05 已由 ADR 0032 解决（design checkpoint `4a654d1`，SHA-record commit `23b112a`）。T038 `READY`。Wave 25 标记 `IN_PROGRESS`，仅授权 T038（Lean Execution Mode）。本轮按 ADR 0032 §§7-9 交付可复现 load-acceptance harness 并驱动 50-VU 两轮测量。

## T038 结果：`REVIEW_FAILED`

新增 `apps/api-python/tests/load/**`（`config.py`/`generator.py`/`scenarios.py`/`collectors.py`/`gates.py`/`report.py`/`stats.py`/`run_load.py`）+ `artifacts/load/**` raw evidence（`run-1/result.json`、`run-2/result.json`、`result.json`、`run.log`）。

### release_verdict: `FAIL`

两轮测量均 FAIL（结构性、确定性，非 flaky）：Run 1 = 11 hard-gate FAIL，Run 2 = 12（增 `db_lock_wait`）。`result.json`："2 run(s) with hard-gate FAIL; 2-of-3 not reached"。无需 Run 3。

### 根因（源码逐行核实，真实 production 容量缺陷，非 harness/env artifact）

1. **主因 — `/api/todos` 无分页**：端点无 `LIMIT`/`OFFSET`，全量 scoped 结果集（11667 todos × 6 表 JOIN）双重物化，返回约 7.4MB。孤立 ~2s，50 VU 并发 p95 6.4–9.5s → 级联连接池耗尽 → 跨类 latency / 5xx（1.4–1.86%）/ 100% task retry / DB slow-query gates（>2s 2262–2623、>500ms 92.4%）/ pool timeout / lock wait。对照 `/api/risks` 有正确分页，孤立 36ms。
2. **SSE 初始事件**：会话创建写 0 个同步 `AgentEvent`，首帧受 ADR 0030 outbox-drain cadence（5s）下界约束，即便无 provider ~5s > 2s gate。跨 ADR 张力（ADR 0030 §3 vs ADR 0032 §6）真实。
3. **SSE heartbeat UNVERIFIED → FAIL**：ADR 0032 §9 fake Provider 无 production 注入点（`build_provider` 无条件），fast-fail 流 ~5s 关闭（`ERROR` terminal），无长流可测 keepalive，`_stream` 无 transport keepalive。

### PASS gates（两轮稳定通过）

§3 worker/queue（queue_age p95 4.58–5.04s、outbox 0s、retry_backlog 0 非单调、worker 100%、无 expired lease）、§4 scheduler（freshness 100%、single-active、cadence ok、tick fail 0%）、§2 task_failed_ratio（0%）、§5 db_disk（<80%）、§6 sse_resume（correct）。ADR 0030 调度器/可靠性拓扑与 outbox/durable-task 体系在负载下稳健；FAIL 集中于 API latency / DB query / SSE 三处 production 容量维度。

### 根因归属（ADR 0032 §10）

| Finding | 归属 owning concern |
|---|---|
| `/api/todos` 无分页（`todos/service.py:62-63,191-198`；`schemas.py:81-85`）→ §1 全类 latency 级联 + §2 5xx/task_retry + §5 slow-query/pool/lock | todos list endpoint owning task（pagination 缺失）+ connection pool 配置 owning concern |
| SSE initial_event drain-bounded（`scheduler.py:64` drain 5s vs §6 ≤2s） | SSE/outbox 架构 owning concern（跨 ADR 0030 §3 vs ADR 0032 §6） |
| SSE heartbeat UNVERIFIED（§9 fake Provider 无注入点 `composition.py:168`；`_stream` 无 keepalive；fast-fail ~5s 关闭） | SSE keepalive + §9 fake-provider 接线 gap owning concern |

production code 未修改（`src/`、schema、index、migration、frozen write-set 全部 0 diff）。缺陷作为 FAIL finding 返回 owning Task，不在 T038 内实施修复。

## Independent Review

`CONFIRMED-FAIL`。独立 Reviewer 对抗性核验全部 claim，确认 FAIL honest、justified、production-untouched：`/api/todos` 真无界（双物化 + 6 表 JOIN 无 LIMIT + dead page params，与 `risks/service.py:401-402` 正确分页不对称）、load model 忠实 §9 且纳入无分页 `/api/todos` 非 methodological flaw（§1 命名"分页列表"，排除它才是 gaming）、SSE 五项子 claim 全部源码核实、production `git diff` 空、无 harness-only 修复可使 gate PASS。标注 4 项 honest caveats（`task_retry` 100% 归属歧义——但 11 项独立 gate FAIL 不依赖它；单请求 timing 未入 artifacts；`db_lock_wait` marginal 1.079s vs 1.0s；3 类 sample-count 不足为池饱和结果）——均不改变 verdict。详见 `docs/implementation/reports/T038.md`。

## Validation

- Ruff（`tests/load/**`）：`All checks passed!`
- mypy（10 files）：`Success: no issues found`
- focused load-test code tests：`16 passed`
- `uv lock --check`：`Resolved 65 packages`
- `git diff --check`：clean
- write-set 合规：仅 `tests/load/**` 新文件 untracked；`src/risk_platform/**`、`alembic/**`、frozen write-set、`infra/docker-compose.yml`、frozen OpenAPI authority 全部 0 diff
- production code 未修改：`git status` 仅 `?? apps/api-python/tests/load/`；`git diff --stat` 空
- reference environment 充分性：16 CPU / 15GB / 无容器限制；驱动期 CPU 0.05–0.20% → I/O/序列化瓶颈非 resource-starved
- external AI/IMAP throughput 未伪造：本轮仅验证本地可测维度；SSE gate 标 `substituted=True`；真实外部 E2E 推迟 T039；无 best-effort PASS on substituted evidence

## code checkpoint

无（FAIL path — 不创建 production code checkpoint；仅交付 `tests/load/**` harness + `artifacts/load/**` raw evidence）。

## 未执行项（按本次授权与协议边界）

- Wave 25 Integration：**未启动**。
- Wave 26 / T039（real external AI/IMAP E2E throughput）：**未启动**。
- capacity/resilience production 修复（`/api/todos` 分页、connection pool 调优、SSE 初始事件/keepalive 架构、§9 fake-provider 接线 gap）：返回 owning Task，不在 T038 内实施。

## T047 remediation（T038 主因 `/api/todos` 分页）

T038 REVIEW_FAILED 主因已由独立 remediation Task **T047** remediate（`REVIEW_PASSED`）：`GET /api/todos` 现在在 **SQL 查询层** 分页（`.offset/.limit` + 独立 `func.count`），消除原 `list()` 双 `_rows()` 全量物化（11667 todos × 6 表 JOIN，~7.4MB）。复用 canonical 分页契约（`items`/`page`/`pageSize`/`total`，与 `RiskQuery` 一致），保留 permissions/DataScope/filter/archived/stable ordering/full-scoped summary+owners/schedule。OpenAPI re-freeze + `openapi.ts` 生成 + 前端 consumer 更新；`contracts:check` clean-tree zero diff。真实 PostgreSQL 16 分页验证 10 tests PASS（含 LIMIT/OFFSET SQL 捕获 + 大数据 fixture + scoped performance smoke <2.0s，**明确标注非 ADR 0032 gate**）。Independent Review `APPROVE-WITH-NITS`（3 项 non-blocking nit，无 blocking finding）。Validation：Ruff/mypy（204 files）/`uv lock --check`/`git diff --check`/contracts+web typecheck/web build/`contracts:check` 全 PASS。

**T038 状态不变 = `REVIEW_FAILED`**。首版 release 目标 = 低并发内部使用；full 50-VU ADR 0032 capacity 认证 DEFERRED 至 post-MVP hardening。DEFERRED findings（首版 MVP 不要求）：50-VU capacity 认证、SSE initial-event p95 ≤2s、SSE heartbeat/transport keepalive、deterministic Provider test seam（§9）、connection-pool 调优（先观察分页修复后是否仍独立）、剩余 API/DB load gates。原 T038 load evidence 未删除；ADR 0032 threshold 未修改；未声称首版 capacity-certified。T038 须在正式 capacity-ready milestone 重跑。

本轮（T047）**未执行**：T038 50-VU 重跑认证；Wave 25 Integration；T039；SSE remediation；connection-pool 调优；ADR 0032 修改。详见 `docs/implementation/reports/T047.md`。
- production code、schema、index、migration、frozen write-set、frozen OpenAPI authority：**未修改**。
