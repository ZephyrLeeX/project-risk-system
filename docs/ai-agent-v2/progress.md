# AI Agent V2 Progress

本文件是跨 Codex 会话的长期交接核心。聊天记录不作为恢复依据。

## Overall Status

`PLANNED`

本次仅完成任务拆解和文档建设，尚未开始 Task 1 功能开发。

## Task Status

| Task | Status | Task file |
|---|---|---|
| Task 1 | `NOT_STARTED` | [`task-01-provider-v2.md`](task-01-provider-v2.md) |
| Task 2 | `NOT_STARTED` | [`task-02-agent-core.md`](task-02-agent-core.md) |
| Task 3 | `NOT_STARTED` | [`task-03-interactions.md`](task-03-interactions.md) |
| Task 4 | `NOT_STARTED` | [`task-04-mutations.md`](task-04-mutations.md) |
| Task 5 | `NOT_STARTED` | [`task-05-integration-cleanup.md`](task-05-integration-cleanup.md) |

## Current Baseline

- branch：`main`
- planning start HEAD SHA：`749371c7697af0869a571fb6456daa5053dc7888`
- date：`2026-08-17`（Asia/Shanghai）
- working tree at planning start：需求文档 `docs/AI Agent 重构需求说明书 v1.0.md` 已存在但未跟踪；除此之外无已报告改动
- planning checkpoint SHA：将在本轮只包含规划文档的 commit 完成后记录于本节或最终交接

Checkpoint 核对规则：开始 Task 时，最近 checkpoint 必须等于当前 `HEAD` 或是当前 `HEAD` 的祖先；若只是祖先，必须审计其后的 commits 与工作区 diff。不得因 SHA 不完全相等而删除或覆盖用户改动。

激活后的状态 authority：`docs/implementation/TASK_GRAPH.md`、assigned `Txxx`、`EXECUTION_STATE.md` 和正式 Task report 决定正式 assigned Task 和机器状态；本文件、各 V2 task `# Status` 与 README current task 只保存详细交接镜像，由 Orchestrator 在 review/checkpoint 后同步。发现不一致时停止并记录 repository state conflict，Implementer 不得自行修改官方状态或选择较先进状态继续。

## Completed Work

### Planning — 2026-08-17

- status：`COMPLETED`
- started：`2026-08-17`
- completed：`2026-08-17`
- checkpoint SHA：`PENDING_COMMIT`
- summary：完成现有 Provider、Agent Core/Tool Registry、Durable Task/SSE、confirmation、RBAC/data scope、Risk/Todo/Project、Admin、Agent frontend、OpenAPI/contracts、migrations 与相关 ADR 的只读审计；建立恰好 5 个串行 Task 的实施文档。
- architecture decisions：记录 Provider/Core 解耦、DeepSeek-only/native Tool Calls、Interaction 等待状态、proposal/commit 隔离、Risk 1:N Todo 与 partial success 的固定边界。
- DB changes：无。
- API changes：无。
- tests：未运行功能测试；本轮仅进行文档结构检查与 `git diff --check`。
- Independent Review：`PASS`。独立 Reviewer 初审发现 2 个 blocking：缺少 Task Graph/assigned `Txxx` activation gate，以及 Task 2 对自然项目识别、地域语义、分析比较与主动风险发现的客观验收不足。两项修订后复核均关闭；恰好 5 Task、线性依赖、mutation 时序、Provider/Core 解耦、Task 5 边界、独立验收、ADR gate 与新会话恢复均通过。
- known limitations：既有 ADR 0005/0019/0020/0028/0029 与 V2 需求存在需要批准的替代/补充关系；各 Task 已设置设计门禁。
- deferred work：全部功能开发、migration、OpenAPI 生成、前端接线和 E2E 均从 Task 1 起按序执行。

后续每完成一个 Task，必须在此追加独立小节，固定记录：status、started/completed、checkpoint SHA、summary、architecture decisions、DB changes、API changes、tests、Independent Review、known limitations、deferred work。

## Current Known Issues

1. `DESIGN_DEVIATION GATE`：V2 DeepSeek-only Provider Account/Model 架构与 ADR 0005/现有 `AiProviderConfig` 不一致；Task 1 功能编码前需批准 V2 ADR/addendum。
2. `DESIGN_DEVIATION GATE`：native Tool Loop 与 ADR 0028/0029 的固定两轮内部 JSON protocol 不一致；Task 2 功能编码前需批准替代契约。
3. `DESIGN_DEVIATION GATE`：统一 editable `AgentInteraction` 与 ADR 0019 的 token-only empty-body confirm 不一致；Task 3/4 编码前需批准 API/persistence/安全语义。
4. `DESIGN_DEVIATION GATE`：六类 mutation、Risk 1:N Todo 和 Project status mutation 超出 ADR 0020 的三命令；Task 4 编码前需批准领域命令 addendum。
5. `ACTIVATION GATE`：本轮未修改 `TASK_GRAPH.md`；实现前 Orchestrator 必须把本计划恰好 5 个主 Task 一一登记/映射为 5 个 assigned `Txxx`。未登记时任何 Task 都是 `BLOCKED`，不得编码。
6. Provider Admin V2 的精确 HTTP path/DTO 尚未由产品需求固定；这是 Task 1 可在 V2 ADR/OpenAPI 兼容边界内确定的技术契约，不得破坏现有 `/api` 消费者。
7. 当前缺少集中 Project Domain Service/status transition policy；Task 4 必须先审计现有 import/legacy 行为，若无法从 authority 得到合法转换规则则报告 `DESIGN_GAP`。
8. 仓库未见现成 browser E2E harness；Task 5 必须建立或明确接入可重复执行的真实 FastAPI/PostgreSQL/Redis/Celery E2E，而不能用 unit mock 冒充全量验收。

## Decisions / Deviations

- 本轮没有修改 ADR、冻结设计或 production code。
- V2 需求作为产品行为 authority 被完整映射到计划，但所有与既有批准 ADR 的差异均显式保留为 gate。
- 旧 Provider 数据不自动迁移为 DeepSeek Official；不设计 dual write。
- Task 5 不承接新的后端业务语义，避免成为无边界清理 Task。

## Next Task

下一 Task：[`task-01-provider-v2.md`](task-01-provider-v2.md)

进入条件：

1. Planning Review 为 `PASS`，planning checkpoint 已记录。
2. 用户明确要求开始/继续 AI Agent V2 的下一个任务。
3. Orchestrator 已将 5 个主 Task 一一登记/映射到 Task Graph 中的 5 个 `Txxx`，并把 Task 1 对应项正式 assigned；未登记时必须 `BLOCKED`。
4. 当前 branch/HEAD/worktree 已按本文件核对，未发现冲突中的实现改动。
5. Task 1 所需 Provider V2 ADR/addendum 已批准；若未批准，Task 1 只能记录 `BLOCKED`/`DESIGN_DEVIATION`，不得编码。

进入后只执行 Task 1；完成、Review、更新本文件并创建 checkpoint 后停止，不自动开始 Task 2。
