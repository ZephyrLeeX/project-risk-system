# Task 3 — AgentInteraction + 项目消歧 + WAITING_FOR_USER

# Status

`NOT_STARTED`

# Goal

建立统一、可持久恢复、一次有效的 `AgentInteraction`，实现项目单选/“以上都不是”/手动输入消歧，并让 Agent 在 `WAITING_FOR_USER` 期间完全释放 Worker 和 Provider timeout。

# Prerequisites

- Task 2 为 `COMPLETED` 且 `REVIEW_PASSED`，checkpoint 已核对。
- 完整阅读需求 §§14–19、37–45、48–49 与本计划/进度/Task 文件。
- 阅读 ADR 0016、0018、0019、0020、0028 以及 Task 1/2 的批准 V2 addenda。
- AgentInteraction persistence/API/SSE/one-use/expiry/ownership 的 ADR 0019 replacement/addendum 已批准。

# Scope

- 新增 Agent execution 业务状态：`RUNNING`、`WAITING_FOR_USER`、`COMPLETED`、`FAILED`、`CANCELLED`，与 DurableTask 状态显式分离。
- 新增可扩展的统一 `AgentInteraction` 模型；本 Task 只实现并登记 `PROJECT_SELECTION`，`WRITE_CONFIRMATION` 由 Task 4 在相同模型上作批准后的增量扩展。
- 多个合理项目候选时持久化唯一、当前用户可见的候选，并发出 `interaction.required` 后正常结束 SSE/当前 worker execution。
- 唯一匹配自动选择；无匹配给出明确结果；手动输入必须再次 `project_search`、权限过滤和实体解析。
- `PROJECT_SELECTION` 只允许一个候选、`以上都不是` 或手工输入；响应 action 支持 `SELECT`、`MANUAL_INPUT`、`CANCEL`。
- 新增 `POST /agent/interactions/{interactionId}/respond`，成功后 enqueue/resume 新 execution 并返回 `streamUrl`。
- interaction ownership、conversation 绑定、状态、一次有效、防 replay、并发响应与 expiry。
- 持久化恢复所需 conversation、intent、interaction、candidate options、必要 bounded context；不得依赖原 Worker 内存。
- `WAITING_FOR_USER` 不占 Worker、不调用 DeepSeek、不累计 provider/tool-loop timeout，可跨刷新稍后继续。
- 新增 SSE `interaction.required` / `interaction.resolved`，遵循 PostgreSQL event fact 和 resume 规则。
- 描述性信息不足的普通文本追问仍以 completed assistant message 结束，下一条 message 通过 conversation context 继续；不滥用复杂 interaction。

# Non-goals

- 不登记或启用 `WRITE_CONFIRMATION`，不生成 MutationDraft，不执行任何 business mutation。
- 不增加 Risk/Todo/Project schema 或 Domain command。
- 不实现批量风险、partial success 或编辑确认；归 Task 4。
- 不修改前端 UI；Task 5 才渲染选择器，但本 Task 必须以 API/SSE integration tests 独立验收。
- 不提前实现 Task 4–5。

# Current implementation impact

- 当前没有 `AgentInteraction` 或 Agent execution 业务状态；conversation 只以 durable task/event 推断执行。
- 当前 confirmation token 是单独模型，不支持项目选择、手动输入、编辑字段或 resume stream。
- 当前 SSE event enum 没有 interaction 事件，前端 reducer 也未处理。
- 当前 `project_list`/V2 `project_search` 可提供 scope-filtered candidates，但需要 interaction 绑定候选快照与重新校验。

# Backend

- Interaction service/repository/policy 与 Agent Core 分离；respond API 不能直接调用 Provider，必须通过 durable outbox enqueue resume。
- interaction 建立、execution 转 WAITING、required event 与当前 task安全终止需定义原子边界，避免 required event 已发但状态不可恢复。
- respond 时重新加载 current identity/RBAC/data scope；候选即使来自旧 snapshot，也必须验证项目仍可见/可用。
- 手动输入作为 untrusted query，不得直接写 projectId。
- 同 conversation 同时只允许批准数量的 open interaction（默认一个 active）；并发 respond 只有一个胜出。

# Frontend

本 Task 不修改前端。通过 API/SSE 测试 fixture 明确 Task 5 所需 UI 状态：single select、none、manual input、submit/cancel、expired/replayed、resume stream。现有前端仍须构建。

# Database impact

- 新增 `agent_executions`（若批准设计采用独立表）与 `agent_interactions`/candidate context 所需表或 JSONB；必须有 owner/conversation/execution FK、type/status/action constraints、created/resolved/expired timestamps、single-active 与 replay 防护。
- `WAITING_FOR_USER` 不映射为 DurableTask `RETRY_WAIT`；等待期间上一 task 已安全终止，resume 创建/关联新的 durable execution attempt，具体关联由批准 ADR 固定。
- interaction 数据纳入 Agent conversation retention；不得因清理破坏仍有效 interaction。
- 不修改业务 domain tables。

# API / Contract impact

- 保留现有 conversation APIs。
- 新增 `POST /api/agent/interactions/{interactionId}/respond`，request action 为 `SELECT`/`MANUAL_INPUT`/`CANCEL`；字段互斥和 strict validation。
- response 至少提供 resolved interaction 与 resume `streamUrl`（取消时按批准 contract 返回 terminal result）；精确 envelope 由批准 ADR/OpenAPI 固定。
- SSE additive event：`interaction.required`、`interaction.resolved`；既有事件字段和 cursor 语义不变。
- 所有 403/404/409/410/422 error code 必须冻结并在前后端 contract 中可判定。

# Security requirements

- interaction 必须属于当前用户和 conversation，一次有效、有状态、防 replay。
- 旧候选不授予权限；respond 时重检 scope，越权不泄露项目存在性。
- manual input 只进入 strict bounded search；候选必须来自真实授权 Project Tool Result。
- open interaction/candidate/context 有数量和大小上限，防数据库与 context 膨胀。
- SSE payload 只含 UI 必要摘要，不泄露内部 tool JSON、权限集合或 provider data。

# Tests

- 唯一候选自动选择；多候选 required；无候选；以上都不是；manual input 命中/多候选/无匹配。
- owner mismatch、conversation mismatch、expired、already resolved、concurrent double response、invalid action/fields。
- interaction 等待期间无 active Worker、无 Provider call、无 timeout 累计；跨进程/页面刷新恢复。
- permission/scope 在创建后撤销；项目 archived/deleted/renamed；respond fail closed。
- durable enqueue/resume、crash/reconcile、SSE required→close→respond→new stream→resolved sequence 和 reconnect。
- 普通信息不足文本追问不创建 interaction。
- OpenAPI/generated contract 和现有 conversation regression。

# Acceptance Criteria

1. 多项目请求产生持久 `PROJECT_SELECTION`，SSE 发 required 后正常结束，execution=`WAITING_FOR_USER` 且无 Worker 占用。
2. 用户刷新或稍后响应可继续原 Agent Task，不需重问原问题。
3. 手动项目名绝不直接变成 projectId，必须经授权 `project_search`；无匹配固定失败。
4. concurrent/replay/owner/scope/expiry 测试证明 interaction 一次有效且不越权。
5. `WAITING_FOR_USER` 与 DurableTask `RETRY_WAIT` 在 schema、service 和测试中明确不同。
6. `interaction.required/resolved` 支持 cursor resume，SSE transport failure 不改变 execution 结论。
7. 零 business mutation，schema/registry/API 中不存在可调用的 `WRITE_CONFIRMATION` 行为。

# Quality Gates

- uv lock check、Ruff、mypy。
- focused interaction/execution/durable/SSE/PostgreSQL concurrency tests。
- 全量后端 pytest、OpenAPI export/check、contracts check、frontend typecheck。
- migration single-head/fresh-upgrade/constraint checks。
- `git diff --check` 和 mutation SQL/domain write negative inspection。

# Independent Review

Reviewer 独立复核 persistence 原子边界、无 Worker 等待、resume 关联、一次有效、权限重检、候选真实来源、SSE 顺序/重连、retention 与无 mutation。重点模拟 double-click、旧权限、Worker crash、SSE断开和手输虚构项目。

# Completion Deliverables

- Interaction ADR/addendum、schema/migration、service/API/SSE contracts。
- execution business state 与 durable task 映射说明。
- project disambiguation/resume implementation 和 tests。
- OpenAPI/generated contract 必要更新、implementation report。
- checkpoint SHA 和 README/progress 更新。

# Handoff to Next Task

Task 4 复用 `AgentInteraction` 承载 `WRITE_CONFIRMATION`，不得另建 token-only旁路。交接列出 interaction create/respond transaction、editable payload extension point、current-auth revalidation hook、execution resume contract、migration head 和 checkpoint。完成后停止，不自动开始 Task 4。
