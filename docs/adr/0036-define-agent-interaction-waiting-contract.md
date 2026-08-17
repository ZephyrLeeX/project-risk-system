# 定义 AgentInteraction、项目消歧与 WAITING_FOR_USER 契约

状态：已批准（T050 addendum）

## Decision

- 新增 PostgreSQL 持久化 `AgentExecution` 与统一 `AgentInteraction`。本 addendum 只登记 `PROJECT_SELECTION`；`WRITE_CONFIRMATION`、`MutationDraft` 和任何业务 mutation 不属于本 Task。
- `AgentExecution.status` 为 `RUNNING`、`WAITING_FOR_USER`、`COMPLETED`、`FAILED`、`CANCELLED`，独立于 ADR 0018 的 `DurableTask.status`。等待用户时当前 durable task 安全结束为终态，绝不使用 `RETRY_WAIT`。
- `PROJECT_SELECTION` 的候选只能来自当前身份调用既有授权 `project_search` 的结果。0 个候选返回无匹配文本；1 个候选自动继续；多个候选创建一次有效 interaction。响应 action 仅为 `SELECT`、`MANUAL_INPUT`、`CANCEL`；单选、支持“以上都不是”，手输必须重新执行授权 `project_search`，不得由客户端提供或推导 `projectId`。
- 创建 interaction、execution 转 `WAITING_FOR_USER`、持久化 bounded resume context 和 `interaction.required` 事件在同一 PostgreSQL transaction 内完成。SSE 仅传递事实，不决定业务状态。
- respond 先锁定并原子消费 open interaction，再重新加载当前用户身份、RBAC 和 project data scope。成功选择只持久化响应并通过 transactional outbox enqueue/resume；respond 路径不调用 Provider。并发响应只有一个成功；owner、conversation、expiry、replay 和当前可见性均 fail-closed。
- resume 使用原始 conversation/user message 与持久 context，不要求用户重输问题。页面刷新、SSE 重连和进程重启只从 PostgreSQL 恢复 pending interaction。
- SSE 增加 `interaction.required` 与 `interaction.resolved`；required 后当前 stream 正常关闭，transport failure 不改变 execution 结论。

## Explicit non-goals

本 addendum 不批准写确认、风险/待办/项目 mutation、proposal/commit tools、`RiskSourceType.AGENT`、前端 UI 或 T051 内容。
