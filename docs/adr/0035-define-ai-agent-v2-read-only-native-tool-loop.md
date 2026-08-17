# 定义 AI Agent V2 只读 Native Tool Loop

状态：已批准（用户于 2026-08-17 明确授权建立本 addendum）

## Context

ADR 0028/0029 为旧 Agent 固定了 `AGENT_PROVIDER_EXECUTION_V1/V2` 的两轮 JSON 协议和
preview/mutation 语义。AI Agent V2 V1 要以 ADR 0034 的 provider-neutral
`AiProviderAdapter` 消费 DeepSeek 官方原生 Tool Calls，并且本 Task 只授权系统内只读查询。
本 addendum 仅冻结 T049 所需的 Scope、native loop、grounding、错误与 durable/SSE 边界；
不设计 Interaction、项目选择暂停、确认或 mutation。

## Decision

### Scope 与工具边界

- V1 Scope 为 `SYSTEM_DATA_ONLY`。独立 `ScopePolicy` 在调用 Provider 或任一业务 Tool 前
  判定 `ALLOWED` 或 `OUT_OF_SCOPE`；后者返回固定范围说明，零业务 Tool invocation、零业务查询，
  且不进入任何写路径。
- Core 的 closed read-only registry 恰为 `project_search`、`project_detail`、
  `risk_category_list`、`risk_list`、`risk_detail`、`todo_list`、`todo_detail`、
  `dashboard_summary`、`dashboard_focus`、`weekly_report`、`weekly_report_detail`。没有 SQL、ORM、
  HTTP、filesystem、Celery 或 mutation capability；本 ADR 不授权 proposal/preview/confirm 工具。
- 每次 invocation 都重新取得当前 identity 并重新验证 permission 与 project data scope；详情越权
  按既有无存在性泄露规则返回 404。历史消息、模型文字和先前 Tool Result 均不能扩大权限。

### Native loop 与 Provider/Core 边界

- Core 只依赖 ADR 0034 的 `AiProviderAdapter`、immutable candidate snapshot 和 typed
  Provider errors；DeepSeek wire 字段、HTTP 状态及 transport 特例只存在 adapter 内。
- Core 发送 provider-neutral assistant/user/tool messages 和闭合 Tool definitions。assistant 的
  typed native tool calls 按 `tool_call_id` 回填 typed `tool` result message；最终无 tool call 的
  assistant text 结束 loop。不得解析、要求或 fallback 到 `AGENT_PROVIDER_EXECUTION_V1/V2`。
- 每个 execution 的 loop 必须配置并硬性限制 `max_model_rounds`、`max_tool_calls`、
  `max_parallel_tool_calls`、`max_total_execution_time`、`max_single_tool_result`、
  `max_total_tool_result` 与 `max_context_size`。同一 tool+canonical arguments 在没有新增 Tool
  context 时重复达到阈值，确定性终止为 loop error。时间预算覆盖模型和工具；超限不截断或猜测事实。
- Core 按 ADR 0034 编排单模型 retry 与 snapshot 内 failover；Tool/RBAC/validation/grounding、
  loop 和 programming errors 不得触发 provider failover。

### Grounding、项目语义与错误

- 每个成功 Tool Result 是 typed、有限大小的事实，具有内部 `toolInvocationId`、`toolName`、
  `dataAsOf` 和安全 provenance；只有本 execution、当前用户授权的 Result 可作为业务事实依据。
  Core 持久化仅保存恢复/grounding 所需的有界数据及 provenance，遵守 retention 与 metadata-only logging。
- 项目及系统业务事实只能来自当前用户授权的 Tool Result。模型可基于这些事实进行分析、比较、
  排序、总结和建议，并须区分 Tool Fact、AI Analysis 与 AI Recommendation。CandidateRisk 必须逐项
  保存和展示 `basisType`（`SYSTEM_FACT`、`AI_ANALYSIS` 或 `MIXED`）与 `evidenceSummary`。
  `SYSTEM_FACT`/`MIXED` 的 `sourceInvocationIds` 只关联实际使用的当前 execution 授权 Tool facts；
  纯 `AI_ANALYSIS` 不伪造 invocation id。AI 的一般风险分析可以提出潜在风险，不能伪装成
  `SYSTEM_FACT`，也不得虚构 Project、系统记录、金额、状态等系统事实。
- 项目全名、简称、别名、不完整名以及地域辅助词只用于构造 `project_search`；最终项目候选必须逐一
  来自当前授权 result。多个候选且尚无 Interaction 时安全回答“需要进一步指定项目”，不得自行选择。
- 错误层级固定为 Provider、scope/loop/grounding、Tool validation/auth/execution、business 与
  internal。对外只给 typed safe code/message/retryability，不记录 prompt、完整 tool arguments/results
  或 raw Provider payload。

### Durable task 与 SSE

- 继续复用 ADR 0018/0019/0020 的 `AGENT_EXECUTION` durable task/outbox、PostgreSQL
  `agent_events`、Celery、lease fencing、reconciliation、SSE resume/heartbeat/backpressure；不得建立
  第二套 task/event 系统。Task 1 candidate snapshot 替代 ADR 0028 的 legacy provider snapshot。
- disconnect、SSE idle close、heartbeat 或 transport cancellation 只能登记 cancellation/transport
  状态，不能单独写业务 execution 的 `FAILED`/`CANCELLED` 终态。只有 fenced Worker 根据持久化
  cancellation、loop 或 Provider outcome 写入业务事件及 durable terminal state。

## Supersession scope

- 对 AI Agent V2/T049 及其后续 V2 Core，本文替代 ADR 0028/0029 中固定 `PLAN/RESPOND`、两轮、
  `AGENT_PROVIDER_EXECUTION_V1/V2`、preview proposal 与 category-option orchestration 的协议部分。
- ADR 0028 的 durable execution、immutable execution configuration、payload minimization、lease
  fencing、PostgreSQL SSE facts 与 composition ownership继续适用，但 provider snapshot 按 ADR 0034。
- ADR 0013、0014、0016、0018、0019、0020、0034 的其余安全、权限、retention、SSE 与 durable
  约束继续适用。Interaction/`WAITING_FOR_USER` 与所有 mutation 仍须后续明确 ADR/Task 授权。

## Consequences

- T049 可实现 ScopePolicy、只读 registry/executor、native bounded loop、grounding/provenance、
  Task 1 snapshot/typed-error 编排及必要的 durable/SSE 修正。
- T049 不得实现 AgentInteraction、`PROJECT_SELECTION`、`WAITING_FOR_USER`、write confirmation、
  risk/todo/project mutation、Risk 1:N Todo、`RiskSourceType.AGENT`、前端重构或 Company adapter。

## T049 domain-read boundary addendum

用户于 2026-08-17 批准解除 T049 的最小领域查询缺口：新增 `ProjectsQueryService`，只拥有
`search(identity, query)` 与 `detail(identity, project_id)`。该 service 自行执行数据库查询和当前
`SessionIdentity` 的 project data-scope 过滤；Agent Tool 仅可调用该 service，不得直接使用
`Project`、`ProjectAlias`、`RiskCategory`、SQLAlchemy 或 ORM。search 只匹配现有 name、alias 与 active
`ProjectAlias`，并使用有界 keyword/page/pageSize，返回真实授权项目的 typed DTO。detail 对不存在或
越权统一返回现有不泄露存在性的 404。

风险分类读取优先复用 `RisksService.filter_options(identity)` 的 active-category 正式能力；必要时可在
`RisksService` 内抽出 typed `list_categories(identity)`，且 filter-options 与 Agent Tool 复用该能力。
本补充不新增 REST API、业务字段、业务推导、mutation、Interaction 或任何 T050 内容。
