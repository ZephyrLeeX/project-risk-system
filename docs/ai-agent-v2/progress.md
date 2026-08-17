# AI Agent V2 Progress

本文件是跨 Codex 会话的长期交接核心。聊天记录不作为恢复依据。

## Overall Status

`IN_PROGRESS`

Task 1 已完成并通过独立 Review；Task 2–5 尚未开始。

## Task Status

| Task | Status | Task file |
|---|---|---|
| Task 1 | `COMPLETED` | [`task-01-provider-v2.md`](task-01-provider-v2.md) |
| Task 2 | `NOT_STARTED` | [`task-02-agent-core.md`](task-02-agent-core.md) |
| Task 3 | `NOT_STARTED` | [`task-03-interactions.md`](task-03-interactions.md) |
| Task 4 | `NOT_STARTED` | [`task-04-mutations.md`](task-04-mutations.md) |
| Task 5 | `NOT_STARTED` | [`task-05-integration-cleanup.md`](task-05-integration-cleanup.md) |

## Current Baseline

- branch：`main`
- planning start HEAD SHA：`749371c7697af0869a571fb6456daa5053dc7888`
- date：`2026-08-17`（Asia/Shanghai）
- working tree at planning start：需求文档 `docs/AI Agent 重构需求说明书 v1.0.md` 已存在但未跟踪；除此之外无已报告改动
- planning content checkpoint SHA：`d9e93c49d4989aab6f6a3f5838f502366dd32bd0`

Checkpoint 核对规则：开始 Task 时，最近 checkpoint 必须等于当前 `HEAD` 或是当前 `HEAD` 的祖先；若只是祖先，必须审计其后的 commits 与工作区 diff。不得因 SHA 不完全相等而删除或覆盖用户改动。

激活后的状态 authority：`docs/implementation/TASK_GRAPH.md`、assigned `Txxx`、`EXECUTION_STATE.md` 和正式 Task report 决定正式 assigned Task 和机器状态；本文件、各 V2 task `# Status` 与 README current task 只保存详细交接镜像，由 Orchestrator 在 review/checkpoint 后同步。发现不一致时停止并记录 repository state conflict，Implementer 不得自行修改官方状态或选择较先进状态继续。

## Completed Work

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
- checkpoint SHA：由紧随 Task 1 code/report checkpoint 的 metadata-only commit 回填
- summary：建立 DeepSeek-only Provider V2；Provider Account 1:N Model Config；厂商无关 adapter/DTO/typed errors；唯一 production `DeepSeekOfficialAdapter`；immutable candidate snapshot；bounded retry/failover；Account/Model health 分离；additive Admin V2/OpenAPI。
- architecture decisions：ADR 0034 批准并替代 ADR 0005/0028 中与 V2 Provider 冲突的部分；固定 `https://api.deepseek.com`、`/models`、`/chat/completions`，实际 socket 固定到 outbound guard revalidated IP，同时保留官方 Host/TLS SNI/证书校验名；不实现 Company adapter 或业务默认 model。
- DB changes：Alembic head `20260817_0010` 新增 `ai_provider_accounts`、`ai_model_configs`、`ai_provider_v2_call_logs` 与三个 enum；旧 Provider 数据保留，无 backfill/dual-write。
- API changes：新增 `/api/admin/ai-provider-v2/accounts/**` Account/Model 管理、discovery/test/status/default/key rotation；旧 `/api/admin/ai-services/**` 保持；OpenAPI 更新为 104 paths / 260 schemas。
- tests：最终 focused `80 passed`；legacy Provider/composition impacted `48 passed`；mail/weekly `28 passed`；uv lock/Ruff/mypy、PostgreSQL fresh migration/check/downgrade-forward、OpenAPI reproducibility、contracts/web typecheck 与 web build、`git diff --check` 均 PASS。
- Independent Review：前两轮共发现 6 项 findings（IP pin、失败 audit、fake HTTPS、strict usage、header casing、missing-account 404），全部修复并补测试；第三轮结论 `REVIEW_PASSED`。
- known limitations：真实 DeepSeek live smoke 因环境无 credential 标记 `BLOCKED_EXTERNAL_INPUTS`，未伪造 PASS；migration 未应用到 production database。
- deferred work：Task 2 才消费 Provider-neutral adapter/candidate snapshot/typed errors；legacy runtime 清理由 Task 5 在零引用证据后处理。

## Current Known Issues

1. `DESIGN_DEVIATION GATE`：native Tool Loop 与 ADR 0028/0029 的固定两轮内部 JSON protocol 不一致；Task 2 功能编码前需批准替代契约。
2. `DESIGN_DEVIATION GATE`：统一 editable `AgentInteraction` 与 ADR 0019 的 token-only empty-body confirm 不一致；Task 3/4 编码前需批准 API/persistence/安全语义。
3. `DESIGN_DEVIATION GATE`：六类 mutation、Risk 1:N Todo 和 Project status mutation 超出 ADR 0020 的三命令；Task 4 编码前需批准领域命令 addendum。
4. 当前缺少集中 Project Domain Service/status transition policy；Task 4 必须先审计现有 import/legacy 行为，若无法从 authority 得到合法转换规则则报告 `DESIGN_GAP`。
5. 仓库未见现成 browser E2E harness；Task 5 必须建立或明确接入可重复执行的真实 FastAPI/PostgreSQL/Redis/Celery E2E，而不能用 unit mock 冒充全量验收。

## Decisions / Deviations

- ADR 0034 已批准并完成 Task 1 Provider V2 边界 reconciliation；实现未偏离该 ADR。
- 其余与既有批准 ADR 的差异继续显式保留为 Task 2–4 gate，不静默融合。
- 旧 Provider 数据不自动迁移为 DeepSeek Official；不设计 dual write。
- Task 5 不承接新的后端业务语义，避免成为无边界清理 Task。

## Next Task

下一 Task：[`task-02-agent-core.md`](task-02-agent-core.md)（当前 `NOT_STARTED`）

进入条件：

1. T048 / Task 1 为 `REVIEW_PASSED / COMPLETED`，checkpoint 已记录。
2. 用户明确要求开始/继续 AI Agent V2 的下一个任务。
3. T049 被正式 assigned，且当前 branch/HEAD/worktree 已按本文件核对。
4. Task 2 的 native Tool Loop / Scope Guard ADR gate 已批准；否则只能记录 `BLOCKED`/`DESIGN_DEVIATION`，不得编码。

进入后只执行 Task 2；完成、Review、更新本文件并创建 checkpoint 后停止，不自动开始 Task 3。
