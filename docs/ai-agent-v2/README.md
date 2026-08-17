# AI Agent V2 实施入口

本目录是 AI Agent V2 重构的长期执行与交接入口。Task 1 已完成并通过独立 Review；Task 2–5 尚未开始。

## Authority 与文档索引

- 产品需求 authority：[`../AI Agent 重构需求说明书 v1.0.md`](../AI%20Agent%20重构需求说明书%20v1.0.md)
- 总体实施计划：[`implementation-plan.md`](implementation-plan.md)
- 长期进度与恢复入口：[`progress.md`](progress.md)
- Task 1：[`task-01-provider-v2.md`](task-01-provider-v2.md)
- Task 2：[`task-02-agent-core.md`](task-02-agent-core.md)
- Task 3：[`task-03-interactions.md`](task-03-interactions.md)
- Task 4：[`task-04-mutations.md`](task-04-mutations.md)
- Task 5：[`task-05-integration-cleanup.md`](task-05-integration-cleanup.md)

若来源冲突，执行者必须遵循仓库 `AGENTS.md` 的优先级：已批准 ADR、冻结设计、`CONTEXT.md`、实施基线与当前 Task。V2 需求是本次产品行为 authority，但它不会静默覆盖既有 ADR。`implementation-plan.md` 已列出需要在功能编码前获得批准的 ADR 更新/替代项。

## 推荐阅读顺序

新的 Codex 会话开始前必须完整阅读：

1. `docs/AI Agent 重构需求说明书 v1.0.md`
2. `docs/ai-agent-v2/implementation-plan.md`
3. `docs/ai-agent-v2/progress.md`
4. `progress.md` 指向的当前 Task 文档

然后只按当前 Task 的 references 加载相关 ADR、设计章节和代码；不要默认加载全部历史报告或全部 Task。

## 当前 Task

当前阶段：`IN_PROGRESS — Task 2 activation`

下一 Task：`Task 2 — Agent Core + Scope Guard + Native Tool Loop`

进入文件：[`task-02-agent-core.md`](task-02-agent-core.md)

Task 2 的 ADR 0035 reconciliation 已完成，当前为 `IN_PROGRESS`；本轮只实施 Task 2，不得开始 Task 3。

仓库 `AGENTS.md` 要求所有 implementation 只能由 `docs/implementation/TASK_GRAPH.md` 中的 assigned `Txxx` 驱动。五个主 Task 已一一登记为 T048–T052；T048 为 `REVIEW_PASSED`，T049 为下一 eligible Task 但仍是 `READY / NOT_STARTED`，不得自动推进。

激活后，`TASK_GRAPH.md`、assigned `Txxx`、`EXECUTION_STATE.md` 和正式 Task report 是正式调度与机器执行 authority；本目录 `progress.md`、各 Task `# Status` 和本 README 的当前 Task 是 V2 详细交接镜像。Orchestrator 在 review/checkpoint 后同步两侧；Implementer 不得自行修改或推进官方状态。两者不一致时停止并按正式 execution state 核对，不能选择较“先进”的状态继续执行。

## 固定执行规则

整个重构恰好包含 5 个主要 Task，按 `Task 1 → Task 2 → Task 3 → Task 4 → Task 5` 串行执行。任何时刻只能实施一个 Task；不得提前实现后续 Task，也不得自动开始下一 Task。

开始任意 Task：

1. 按“推荐阅读顺序”恢复上下文。
2. 检查 `git status`、当前 branch、`HEAD`，并按 `progress.md` 核对最近 checkpoint 的祖先关系与未提交改动。
3. 检查当前 Task 已一一映射为 Task Graph 中的 assigned `Txxx`、前置 Task 为 `COMPLETED`，且设计门禁满足。
4. 将当前 Task 的 `# Status` 与 `progress.md` 同步为 `IN_PROGRESS`，记录 started 时间和起始 SHA。
5. 只实施当前 Task 的 Scope；遇到 stop condition 立即按下文处理。

完成任意 Task：

1. 客观满足全部 Acceptance Criteria。
2. 跑完全部 Quality Gates，并记录实际命令和结果。
3. 由未参与实现的 Reviewer 完成 Independent Review。
4. 修复 findings 后重新运行受影响的 gates；若无法修复，保持 `BLOCKED` 或 review failure，不得声称完成。
5. 创建只包含当前 Task 范围的 checkpoint commit，记录 SHA。
6. 将当前 Task 标记为 `COMPLETED`，更新 `progress.md` 的完成记录。
7. 将本文件“当前 Task”指向下一 Task；Task 5 完成时改为 `ALL_TASKS_COMPLETED`。
8. 停止。禁止自动开始下一 Task。

## 新会话恢复协议

如果用户在全新 Codex 会话只说“继续 AI Agent V2 的下一个任务”，Codex 必须自行：

1. 找到本 README。
2. 完整阅读需求文档、总体计划、进度文档和当前 Task。
3. 核对 progress 中最近 checkpoint 是否为当前 `HEAD` 或其祖先；检查 checkpoint 之后的提交和工作区改动是否与当前 Task 相容。
4. 检查 Task Graph/assigned `Txxx` 登记、前置 Task、设计门禁和外部依赖。
5. 只执行一个 Task，完成并更新文档后停止。

不要求用户重新提供旧聊天内容；聊天记忆不是 authority。

## 产品歧义与偏离处理

- 纯技术实现问题可在冻结需求与批准架构范围内自行设计，并在 Task report 记录。
- 产品行为未定义、需求冲突、权限语义变化、人工确认规则变化或 partial success 语义变化：将当前 Task 标记为 `BLOCKED`，在 `progress.md` 记录问题并询问用户。
- 实现需要偏离批准 ADR/冻结设计：记录 `DESIGN_DEVIATION` 并等待批准。
- 批准来源不足以作出必要决定：记录 `DESIGN_GAP` 并停止相关实现。
