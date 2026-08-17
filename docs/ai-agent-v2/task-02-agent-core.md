# Task 2 — 只读 Agent Core + Native Tool Loop

# Status

`IN_PROGRESS`

# Goal

以 Task 1 的厂商无关 adapter 重建只读 Agent Core：先做独立 scope 判断，再通过 DeepSeek native Tool Calls 执行有界多轮工具循环，并保证所有业务事实来自当前用户授权的 typed Tool Result。

# Prerequisites

- Task 1 为 `COMPLETED` 且 `REVIEW_PASSED`，checkpoint 已核对。
- 完整阅读需求 §§1、7–16、19–23、38、40、45–49、51、53，以及本计划/进度/Task 文件。
- 阅读 ADR 0004、0013、0014、0016、0018、0019、0020、0028、0029 和 Task 1 批准的 V2 ADR。
- native Tool Loop/执行配置/error/SSE 关系的 V2 ADR 已批准，明确替代 ADR 0028/0029 的固定两轮内部 JSON protocol。

# Scope

- 独立 `ScopePolicy`，至少返回 `ALLOWED`/`OUT_OF_SCOPE`；未来 `PROJECT_KNOWLEDGE` 可扩展但 V1 不实现。
- Out-of-scope 在任何业务 Tool 前固定短答，不查询业务数据、不进入 mutation。
- 使用 DeepSeek Chat Completions `assistant.tool_calls` / `tool` messages 驱动多轮 loop，不要求模型输出项目内部 orchestration JSON。
- 配置并测试 `max_model_rounds`、`max_tool_calls`、`max_parallel_tool_calls`、`max_total_execution_time`、`max_single_tool_result`、`max_total_tool_result`、`max_context_size` 与重复调用检测。
- 完成 read-only Tool Registry：`project_search`、`project_detail`、`risk_category_list`、`risk_list`、`risk_detail`、`todo_list`、`todo_detail`、`dashboard_summary`、`dashboard_focus`、`weekly_report`、`weekly_report_detail`。
- 实现自然项目识别：项目全名、简称、别名和不完整名称通过授权 Tool 解析为真实 `projectId/projectName`；模型可用地域语义辅助扩展 search terms（例如无锡→锡山/新吴/惠山/锡东），但最终候选只能来自当前用户可见的 `project_search` Tool Result。
- 支持基于授权系统数据的总结、归纳、比较、排序和主动风险发现；CandidateRisk 按 `SYSTEM_FACT`、`AI_ANALYSIS`、`MIXED` 区分依据。系统事实只能来自当前 execution 的授权 Tool Result；`AI_ANALYSIS` 可以没有系统异常证据，但必须明确标注为 AI 风险分析。
- 每次 invocation 记录内部 `toolInvocationId`、`toolName`、`dataAsOf`、typed result，并携带安全 provenance。
- 当前 identity、permission 和 data scope 每次工具调用都重取/重检；历史 conversation 不扩大范围。
- 稳定候选模型 snapshot、单模型 retry/跨模型 failover 由 Core 按 Task 1 contract 编排；Provider 特例仍留在 adapter。
- 复用现有 `AGENT_EXECUTION` durable task/outbox、Celery worker、PostgreSQL events、SSE resume/heartbeat/backpressure；修正 transport 与 business status 边界。
- 保持现有 conversation create/continue/history/messages/help API。
- 实现明确错误层次：Provider、Agent scope/loop/grounding、Tool validation/auth/execution、business/internal。

# Non-goals

- 不实现 `AgentInteraction`、项目选择 UI/API 或 `WAITING_FOR_USER` 暂停恢复；归 Task 3。
- 不把项目多候选自动选成一个；在 Task 3 前只读 Core 可返回明确“需要进一步指定项目”的安全文本，不创建 interaction。
- 不向模型暴露任何 proposal/mutation/commit tool，不创建 Risk/Todo/Project。
- 不修改 Risk 1:N Todo、Risk source enum 或 Domain mutation service；归 Task 4。
- 不修改 Agent/Admin 前端；归 Task 5。
- 不提前实现 Task 3–5。

# Current implementation impact

- `agent.execution` 当前固定两轮 `PLAN/RESPOND`，验证 `AGENT_PROVIDER_EXECUTION_V2` action JSON；该路径由 native loop 替代。
- composition 当前对 Responses native function calls 做中间归一化，并可回退 text JSON；V2 DeepSeek path 不保留这套 orchestration fallback。
- 现有 registry 有 9 个工具，需补 `project_detail`、`risk_category_list`，并将 `project_list` 对齐为 `project_search`。
- 现有 conversation、event、durable task、SSE 基础可复用；不得另建第二套 task/event 系统。
- 当前 `AgentExecutionConfig` 单模型 snapshot 需按 Task 1 候选模型 contract 调整，但 Provider Account/Model schema 不在本 Task修改。

# Backend

- Core 仅依赖 `AiProviderAdapter` 和 domain-facing registry protocol。
- Tool executor 固定顺序：registry lookup → Pydantic strict validation → tool authorization → RBAC → data scope → domain service → typed result。
- 禁止 ad-hoc 跨模块表查询；缺少授权 domain query service 时报告 `DESIGN_GAP`，不能在 Agent 内直接 SQL。
- AI analysis/recommendation 必须与 Tool Fact 可区分；CandidateRisk 输出必须符合冻结的最小结构化契约；本 Task 不生成 mutation draft。
- free-text 补充继续作为新的 conversation message 持久化；上下文有明确总量上限。
- provider response、tool arguments/results 只按最小必要持久化；遵守 retention 和 metadata-only logging。

# Frontend

本 Task 不修改前端。现有客户端应继续消费 `progress`、`message.delta`、`completed`、`error`、`heartbeat`；不得引入需要 Task 5 UI 才能完成只读验收的隐藏依赖。

# Database impact

- 可对 Agent execution snapshot、tool invocation/provenance 持久化增加 V2 所需表/字段和一条有序 migration，但不得创建第二套 durable task/event 表。
- 是否持久化完整 Tool Result 必须遵守数据最小化与 90 天 conversation retention；默认只保存恢复/grounding 所需的有界 typed data/provenance。
- 不修改 Risk、Todo、Project schema。

# API / Contract impact

- 保持四个 conversation API、messages page、help 和 SSE endpoint path/基础 event shape。
- `GET /agent/help` 更新为 V2 read-tool catalogue。
- 只允许向现有 event/error contract做向后兼容扩展；`interaction.*` 延后 Task 3。
- OpenAPI 和 generated types 必须可重复生成且现有前端 typecheck 不回归。

# Security requirements

- Scope Guard 必须先于 Tool；out-of-scope 断言零 Tool invocation。
- 每个 Tool 的 current identity/RBAC/data scope 负向测试必须覆盖，越权 detail 返回不泄露存在性的 404。
- conversation/history/tool data 都视为 prompt injection 数据，不能改变 system/tool policy。
- 重复相同 tool+arguments 且无新增上下文达到阈值时终止；不得无限 loop。
- 模型没有 SQL/ORM/HTTP/filesystem/Celery/mutation 权限。
- 日志不含完整 prompt/history/tool arguments/result/provider response。

# Tests

- Scope policy：系统业务、out-of-scope、边界表达；out-of-scope 零工具调用。
- Native loop：0/1/多轮 tool calls、parallel 上限、tool message correlation、final response。
- 全部 query tools 的 schema、typed adapters、RBAC × data scope matrix、not-found、防越权 counts。
- grounding：无 Tool 事实时不得陈述系统业务事实；`SYSTEM_FACT`/`MIXED` 的 CandidateRisk 无有效 provenance fail closed，`AI_ANALYSIS` 必须显式说明为 AI 风险分析。
- 实体/地域场景：全名、别名、简称、不完整名、无锡等地域表达；断言模型扩展词本身不能生成 Project，所有返回项目均属于授权 Tool Result，scope 外同地域项目不可见。
- 分析场景：跨项目比较/排序、项目风险总结、“这个项目有什么风险值得上报”；断言结论区分 Tool Fact/AI Analysis/AI Recommendation，系统事实依据逐项引用当前 execution 的有效 invocation IDs，AI_ANALYSIS 的来源为空且明确标注 AI 风险分析。
- limits：每个 limit、总时间、result/context size、重复 call 检测。
- provider failover 与业务/tool错误不 failover 的 integration tests。
- durable worker：retry、lease fencing、cancel、restart/reconcile；SSE reconnect、heartbeat、backpressure、idle transport 不改业务终态。
- current conversation API/OpenAPI/front-end typecheck regression。

# Acceptance Criteria

1. production Agent path不再解析或要求 `AGENT_PROVIDER_EXECUTION_V1/V2` action JSON，且使用 DeepSeek native tool calls。
2. 所列 11 个 query tools 均通过 typed contract 和权限/范围负向测试。
3. out-of-scope 在零 Tool/零业务查询条件下返回固定范围说明。
4. 所有限制均配置化、有合理默认和边界测试；重复 loop 可确定终止。
5. 每项系统业务事实可追溯至本 execution 的授权 Tool Result；CandidateRisk 按 basisType 执行最小契约和 provenance 校验，AI_ANALYSIS 不因缺少系统异常证据而被拒绝，但不得伪装为系统事实。
6. 项目全名/别名/简称/不完整名和地域语义场景均能解析；模型提出的任何候选都必须与授权 `project_search` 结果一一对应，不可凭地理常识虚构或越权。
7. 比较、排序、风险分析和主动风险发现有客观场景测试；CandidateRisk 的系统事实与 AI 推理可区分，AI_ANALYSIS 可在无系统异常证据时成立并明确标注。
8. SSE disconnect/idle/reconnect 不写伪业务失败；PostgreSQL 仍是 event/task fact source。
9. Provider 特有字段/状态判断不出现在 Agent Core。
10. conversation `/api` contract 兼容且无 mutation/interaction/前端越界改动。

# Quality Gates

- 当前仓库版本下执行 uv lock check、Ruff、mypy。
- focused Agent Core/tool/provider/durable/SSE tests，PostgreSQL integration tests。
- 全量后端 pytest；若存在已知非本 Task flaky，必须给出独立复现证据，不得静默忽略。
- OpenAPI export/check、contracts check、frontend typecheck。
- `git diff --check` 与禁止旧 protocol/任意 tool 的静态搜索。

# Independent Review

Reviewer 必须验证 Provider/Core 解耦、native loop wire contract、scope-before-tool、grounding、完整 query capability、所有 limits、RBAC/data scope、durable/SSE 边界和错误分类。重点寻找旧 JSON fallback、直接 SQL、超权查询、mutation tool、无限 loop 和 SSE 写业务状态。结论必须可复现。

# Completion Deliverables

- 批准的 native loop ADR/addendum 与 implementation report。
- Scope Policy、Agent Core、read Tool Registry/Executor、provenance 和 typed errors。
- durable execution/SSE integration 与 migration（如需要）。
- 完整 tests、OpenAPI/contract artifacts。
- checkpoint SHA 和 README/progress 状态更新。

# Handoff to Next Task

Task 3 复用 conversation、execution、native loop、read tools、provenance 和 SSE persistence，只增加等待/恢复 interaction。交接必须列出 execution state extension points、pending context最小集合、SSE append interface、错误 contract、migration head 和 checkpoint。完成后停止，不自动开始 Task 3。
