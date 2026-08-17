# AI Agent V2 Progress

本文件是跨 Codex 会话的长期交接核心。聊天记录不作为恢复依据。

## Overall Status

`COMPLETED` / T050 `REVIEW_PASSED`

Task 1–3 已完成并通过独立 Review；Task 4–5 尚未开始。

## Task Status

| Task | Status | Task file |
|---|---|---|
| Task 1 | `COMPLETED` | [`task-01-provider-v2.md`](task-01-provider-v2.md) |
| Task 2 | `COMPLETED` / `REVIEW_PASSED` | [`task-02-agent-core.md`](task-02-agent-core.md) |
| Task 3 | `COMPLETED` / `REVIEW_PASSED` | [`task-03-interactions.md`](task-03-interactions.md) |
| Task 4 | `NOT_STARTED` | [`task-04-mutations.md`](task-04-mutations.md) |
| Task 5 | `NOT_STARTED` | [`task-05-integration-cleanup.md`](task-05-integration-cleanup.md) |

## Current Baseline

- branch：`main`
- planning start HEAD SHA：`749371c7697af0869a571fb6456daa5053dc7888`
- date：`2026-08-17`（Asia/Shanghai）
- working tree at T050 completion：code checkpoint `85dc4bc29f2de19f7a874d2a6c934e68cff8d5f0`；metadata checkpoint follows this report update
- planning content checkpoint SHA：`d9e93c49d4989aab6f6a3f5838f502366dd32bd0`

Checkpoint 核对规则：开始 Task 时，最近 checkpoint 必须等于当前 `HEAD` 或是当前 `HEAD` 的祖先；若只是祖先，必须审计其后的 commits 与工作区 diff。不得因 SHA 不完全相等而删除或覆盖用户改动。

激活后的状态 authority：`docs/implementation/TASK_GRAPH.md`、assigned `Txxx`、`EXECUTION_STATE.md` 和正式 Task report 决定正式 assigned Task 和机器状态；本文件、各 V2 task `# Status` 与 README current task 只保存详细交接镜像，由 Orchestrator 在 review/checkpoint 后同步。发现不一致时停止并记录 repository state conflict，Implementer 不得自行修改官方状态或选择较先进状态继续。

## Completed Work

### Task 2 completion — 2026-08-17

- status：`COMPLETED` / mapped T049 `REVIEW_PASSED`
- ADR：新增 ADR 0035，冻结只读 Scope Policy、native Tool Loop、limits、grounding、Provider/Core、error taxonomy 与 durable/SSE 边界；没有设计 Interaction 或 mutation。
- toolchain：已使用环境中已安装的 `uv@0.12.3` 与 `python@3.12.13` 执行 focused Ruff，结果 PASS；未下载或改变依赖。
- implementation/tests/checkpoint：Native worker durable lifecycle、V2 read-only boundary 与 test baseline migration 完成；code checkpoint `3813163fca3d076c08a8ec9f5b5a69c9016c9678`；未开始后续 Task。
- summary：5 个 legacy worker/V1 失败测试迁移至 NativeAgentExecutionWorker/PostgreSQL durable task、Agent events/SSE 不变量；T030 preview/category-stale mutation 测试改为 V2 read-only boundary，mutation/category revalidation deferred to T051。
- architecture decisions：沿用 ADR 0035 native Tool Loop、Provider/Core 解耦、scope-before-tool、PostgreSQL event/task authority；未恢复旧 PLAN/RESPOND、legacy snapshot、preview/mutation V1 orchestration。
- DB changes：无新 migration；同步 `agent_messages.structured` 的 metadata/schema baseline，Alembic head `20260817_0011`。
- API changes：无 breaking API change；OpenAPI/generated contract reproducibly aligned。
- tests：focused `24 passed`；full PostgreSQL 16 + Redis 7 pytest `513 passed, 1 skipped`；Ruff/mypy/uv lock/Alembic/OpenAPI/contracts/frontend typecheck/git diff check PASS。
- Independent Review：`REVIEW_PASSED`，无 blocking finding。
- known limitations：真实 DeepSeek credential/live smoke 仍按 T048 标记为外部输入，不伪造 PASS；legacy implementation cleanup 留给 T052。
- deferred work：AgentInteraction/project disambiguation→T050；confirmed mutation/category revalidation→T051；frontend/Admin cutover与legacy cleanup→T052。

### Planning — 2026-08-17

- status：`COMPLETED`
- started：`2026-08-17`
- completed：`2026-08-17`
- checkpoint SHA：`d9e93c49d4989aab6f6a3f5838f502366dd32bd0`
- summary：完成现有 Provider、Agent Core/Tool Registry、Durable Task/SSE、confirmation、RBAC/data scope、Risk/Todo/Project、Admin、Agent frontend、OpenAPI/contracts、migrations 与相关 ADR 的只读审计；建立恰好 5 个串行 Task 的实施文档。
- architecture decisions：记录 Provider/Core 解耦、DeepSeek-only/native Tool Calls、Interaction 等待状态、proposal/commit 隔离、Risk 1:N Todo 与 partial success 的固定边界。
- DB changes：无。
- API changes：无。
- tests：未运行功能测试；本轮仅进行文档结构检查与 `git diff --check`。
- Independent Review：`PASS`。独立 Reviewer 初审发现 2 个 blocking：缺少 Task Graph/assigned `Txxx` activation gate，以及 Task 2 对自然项目识别、地域语义、分析比较与主动风险发现的客观验收不足。两项修订后复核均关闭；恰好 5 Task、线性依赖、mutation 时序、Provider/Core 解耦、Task 5 边界、独立验收、ADR gate 与新会话恢复均通过。
- known limitations：既有 ADR 0005/0019/0020/0028/0029 与 V2 需求存在需要批准的替代/补充关系；各 Task 已设置设计门禁。
- deferred work：全部功能开发、migration、OpenAPI 生成、前端接线和 E2E 均从 Task 1 起按序执行。

后续每完成一个 Task，必须在此追加独立小节，固定记录：status、started/completed、checkpoint SHA、summary、architecture decisions、DB changes、API changes、tests、Independent Review、known limitations、deferred work。

### Task 1 — Provider V2 + DeepSeek Official Adapter — 2026-08-17

- status：`COMPLETED` / mapped T048 `REVIEW_PASSED`
- started/completed：`2026-08-17` / `2026-08-17`
- checkpoint SHA：`31e84f1e9dcecee51f6ded41ef9307c4dfe49960`（由紧随 checkpoint 的 metadata-only commit 回填）
- summary：建立 DeepSeek-only Provider V2；Provider Account 1:N Model Config；厂商无关 adapter/DTO/typed errors；唯一 production `DeepSeekOfficialAdapter`；immutable candidate snapshot；bounded retry/failover；Account/Model health 分离；additive Admin V2/OpenAPI。
- architecture decisions：ADR 0034 批准并替代 ADR 0005/0028 中与 V2 Provider 冲突的部分；固定 `https://api.deepseek.com`、`/models`、`/chat/completions`，实际 socket 固定到 outbound guard revalidated IP，同时保留官方 Host/TLS SNI/证书校验名；不实现 Company adapter 或业务默认 model。
- DB changes：Alembic head `20260817_0010` 新增 `ai_provider_accounts`、`ai_model_configs`、`ai_provider_v2_call_logs` 与三个 enum；旧 Provider 数据保留，无 backfill/dual-write。
- API changes：新增 `/api/admin/ai-provider-v2/accounts/**` Account/Model 管理、discovery/test/status/default/key rotation；旧 `/api/admin/ai-services/**` 保持；OpenAPI 更新为 104 paths / 260 schemas。
- tests：最终 focused `80 passed`；legacy Provider/composition impacted `48 passed`；mail/weekly `28 passed`；uv lock/Ruff/mypy、PostgreSQL fresh migration/check/downgrade-forward、OpenAPI reproducibility、contracts/web typecheck 与 web build、`git diff --check` 均 PASS。
- Independent Review：前两轮共发现 6 项 findings（IP pin、失败 audit、fake HTTPS、strict usage、header casing、missing-account 404），全部修复并补测试；第三轮结论 `REVIEW_PASSED`。
- known limitations：真实 DeepSeek live smoke 因环境无 credential 标记 `BLOCKED_EXTERNAL_INPUTS`，未伪造 PASS；migration 未应用到 production database。
- deferred work：Task 2 才消费 Provider-neutral adapter/candidate snapshot/typed errors；legacy runtime 清理由 Task 5 在零引用证据后处理。

## Current Known Issues

1. ADR 0036 已批准并完成 T050 的统一 `AgentInteraction` / respond / persistence / SSE replacement boundary；T051 仍需其自身 WRITE_CONFIRMATION addendum。
2. `DESIGN_DEVIATION GATE`：六类 mutation、Risk 1:N Todo 和 Project status mutation 超出 ADR 0020 的三命令；Task 4 编码前需批准领域命令 addendum。
3. 当前缺少集中 Project Domain Service/status transition policy；Task 4 必须先审计现有 import/legacy 行为，若无法从 authority 得到合法转换规则则报告 `DESIGN_GAP`。
4. 仓库未见现成 browser E2E harness；Task 5 必须建立或明确接入可重复执行的真实 FastAPI/PostgreSQL/Redis/Celery E2E，而不能用 unit mock 冒充全量验收。

## Decisions / Deviations

- ADR 0034 已批准并完成 Task 1 Provider V2 边界 reconciliation；实现未偏离该 ADR。
- T051 的 mutation/WRITE_CONFIRMATION 设计门禁继续显式保留，不在 T050 静默实现。
- 旧 Provider 数据不自动迁移为 DeepSeek Official；不设计 dual write。
- Task 5 不承接新的后端业务语义，避免成为无边界清理 Task。

## Next Task

下一 Task：[`task-04-mutations.md`](task-04-mutations.md)（T051，`READY`；本轮未开始）

进入条件：

1. T048 / Task 1 为 `REVIEW_PASSED / COMPLETED`，checkpoint 已记录。
2. 用户明确要求开始/继续 AI Agent V2 的下一个任务。
3. T050 已正式完成，且当前 branch/HEAD/worktree 已按本文件核对。
4. T050 已由 ADR 0036 和本轮用户批准语义授权并完成；T051 需新的明确启动与 mutation addendum。

T050 已完成；本轮在 T051 前停止，不自动开始下一 Task。

### Task 3 / T050 completion — 2026-08-17

- status：`COMPLETED` / mapped T050 `REVIEW_PASSED`
- started/completed：`2026-08-17` / `2026-08-17`
- checkpoint SHA：`85dc4bc29f2de19f7a874d2a6c934e68cff8d5f0`
- summary：完成统一 `AgentInteraction`、`AgentExecution` 业务状态、PROJECT_SELECTION 项目消歧、严格 SELECT/MANUAL_INPUT/CANCEL respond、手输重新 scoped `project_search`、one-use/expiry/replay/ownership/concurrency、防权限变化、durable outbox resume、reload/restart recovery 与 SSE `interaction.required/resolved`。
- architecture decisions：ADR 0036 批准并冻结 `WAITING_FOR_USER` 与 `DurableTask.RETRY_WAIT` 分离；等待不占 Worker、不调用 Provider、不累计 timeout；respond 不直接调用 Provider；T050 只登记 PROJECT_SELECTION，不登记 WRITE_CONFIRMATION。
- DB changes：migration `20260817_0012` 新增 `agent_executions`、`agent_interactions`，扩展 AgentEventType enum，取消 execution config 对 user message 的错误唯一限制；Alembic fresh upgrade/single-head/autogenerate PASS。
- API changes：新增 `POST /api/agent/interactions/{interactionId}/respond`；OpenAPI/generated contract 同步；前端 UI 未修改。
- tests：focused `23 passed`；T050 regression focus `13 passed`；full pytest `464 passed, 52 skipped`；Ruff/mypy/uv lock、OpenAPI export/gen、frontend typecheck/build、git diff check PASS。
- Independent Review：`REVIEW_PASSED`，无 blocking finding。
- known limitations：真实 Provider live credential smoke 仍沿用 T048 的外部输入限制；前端交互 UI 与 WRITE_CONFIRMATION 留给 T051/T052。
- deferred work：T051 confirmed writes + MutationDraft；本轮未开始 T051。

T050 已完成；本轮在 T051 前停止，不自动开始下一 Task。
