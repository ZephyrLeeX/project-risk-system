# AI Agent V2 分阶段实施计划

## 1. 计划状态与范围

- 状态：`PLANNED`
- 产品 authority：`docs/AI Agent 重构需求说明书 v1.0.md`
- Task 数量：恰好 5 个
- 执行方式：严格串行；一个 Task 完成、Review 通过、形成 checkpoint 并更新进度后停止
- 本计划不授权本轮开展功能开发
- 本计划文档不是 `TASK_GRAPH.md` 的替代品；实现前须由 Orchestrator 将恰好 5 个主 Task 一一登记/映射为 assigned `Txxx`

## 2. 当前实现审计摘要

| 边界 | 当前 production 实现 | V2 目标差异 | 主要归属 |
|---|---|---|---|
| Provider | `ai_provider_configs` 将 endpoint、protocol、model、key、策略放在一行；支持多种 OpenAI-compatible protocol | Provider Account 与 Model Config 分层；V1 仅 `DEEPSEEK_OFFICIAL`；固定官方边界；候选模型快照与 failover | Task 1 |
| Provider transport | `AiProviderClient` 支持 Chat Completions、Responses、Anthropic；Agent adapter 位于 composition | `DeepSeekOfficialAdapter` 直接使用官方 `/chat/completions` 与 `/models`，Core 只依赖 `AiProviderAdapter` | Task 1 |
| Agent Core | `AGENT_PROVIDER_EXECUTION_V2` 内部 JSON；固定 `PLAN → RESPOND` 两轮；部分 Responses native call 被归一化回内部 action | DeepSeek native Tool Calls 驱动有界多轮 loop；独立 Scope Policy；细分错误分类；事实 grounding | Task 2 |
| Query tools | 已有 9 个只读工具；`project_list` 缺少要求中的 `project_detail`，命名/结果需 V2 对齐 | 完整受限查询目录、Pydantic 校验、RBAC/data scope、typed result/provenance | Task 2 |
| Durable/SSE | `AGENT_EXECUTION` 使用 Celery/Redis delivery、PostgreSQL task/event facts；已有 resume、heartbeat 与 backpressure | 保留 durable 基础；execution 业务状态与 task 状态分离；SSE 不决定 execution 成败 | Task 2/3 |
| Confirmation | `agent_confirmation_tokens` + `POST /agent/confirmations/{token}`；固定 `REPORT/PROCESS/RESOLVE`，客户端不能编辑 payload | 统一 `AgentInteraction`；`respond` 支持选择、手输、可编辑确认、取消；一次有效并重校验 | Task 3/4 |
| Risk/Todo/Project | Risk 创建自动产生唯一 Todo；`action_items.riskId` 唯一；无集中 Project status command；Agent mutation 仅三种 | Risk 1:N Todo，最多一个默认 Todo；`AGENT` source；六类 proposal；批量 Risk partial success | Task 4 |
| Admin/前端/契约 | Admin 仍是单层 Provider UI；Agent UI 只处理 preview/token；OpenAPI 已是 TS authority | Provider Account/Model UI、interaction UI、SSE 新事件、生成契约、全量 E2E | Task 5 |

历史 NestJS/Prisma 仅用于解释旧行为和 contract，不属于 V2 runtime，不允许双写或 FastAPI runtime 调用 NestJS。

## 3. 最终目标架构

```text
Vue Agent/Admin UI
  ├─ Conversation REST + resumable SSE
  └─ Interaction respond REST
                │
                ▼
FastAPI Agent module
  ├─ Scope Policy
  ├─ Conversation / Execution / Interaction
  ├─ bounded native Tool Loop
  ├─ Read Tool Registry ──► authorized Domain Services
  └─ Proposal Tools ──────► MutationDraft only
                │
                ▼
AiProviderAdapter
  └─ DeepSeekOfficialAdapter (V1 only)
                │
                ▼
DeepSeek Official /chat/completions + native tool_calls

Celery Worker executes RUNNING work
PostgreSQL owns tasks, events, interactions, drafts and business facts
Redis is delivery transport only

Confirmed AgentInteraction
  └─ Server Commit Handler (never exposed to model)
       └─ Risk / Todo / Project Domain Service + typed audit
```

## 4. 五个 Task 与依赖

| Task | 名称 | 一句话目标 | 依赖 |
|---|---|---|---|
| 1 | Provider V2 + DeepSeek Official Adapter | 建立 Provider Account/Model Config 分层和唯一的 DeepSeek Official 适配器，为 Core 提供稳定候选模型快照与安全 failover 元数据。 | 无 V2 Task 依赖；先过 ADR gate |
| 2 | 只读 Agent Core + Native Tool Loop | 用 DeepSeek native Tool Calls 重建只读、可控、有 grounding 的 Agent loop，并复用 durable task、SSE 和授权领域服务。 | Task 1 |
| 3 | AgentInteraction + 项目消歧 + WAITING_FOR_USER | 持久化业务等待状态，实现项目单选/手输消歧与无 Worker 占用的恢复执行。 | Task 2 |
| 4 | 写操作 + 人工确认 + Mutation | 通过 proposal → editable confirmation → server commit 实现全部批准 mutation、Risk 1:N Todo 和批量 Risk partial success。 | Task 3 |
| 5 | 前端/Admin 收口 + 旧架构清理 + 全量 E2E | 接通新 Provider/Interaction UI、冻结 OpenAPI 契约、删除已替代 Agent V1 路径并完成端到端验收。 | Task 4 |

唯一依赖链：`Task 1 → Task 2 → Task 3 → Task 4 → Task 5`。不存在反向依赖或循环依赖。

## 5. 全局不变量

1. V1 只支持 `DEEPSEEK_OFFICIAL`；Company API 只保留未来 adapter 扩展能力，不实现、不伪装成 DeepSeek。
2. Provider Adapter 与 Agent Core 解耦；Core 不判断 DeepSeek HTTP 字段、厂商错误或 endpoint 特例。
3. 主路径使用 DeepSeek native Tool Calls，不以项目内部 JSON action 协议编排工具。
4. 业务事实必须来自当前用户授权的 Tool Result；分析与建议必须可区分于事实。
5. 所有 Tool 继承当前登录用户 RBAC 与 project data scope；无 Agent superuser/bypass。
6. 模型永远不能直接执行 mutation，也不能获得 commit handler、SQL、ORM、shell、filesystem、HTTP 或任意数据库工具。
7. 所有写操作必须由真实用户显式确认；`NO CONFIRMATION = NO MUTATION`。
8. `WAITING_FOR_USER` 是 Agent execution 业务状态，不是 `DurableTask.RETRY_WAIT`；等待时不占 Worker、不调用 Provider、不累计 execution timeout。
9. SSE 只是 delivery channel；连接 idle、断开或重连不改变 Agent execution 的业务成败。
10. CandidateRisk 是 Agent 分析产物，`basisType` 可为 `SYSTEM_FACT`、`AI_ANALYSIS` 或 `MIXED`。系统事实只能来自当前用户授权 Tool Result；`AI_ANALYSIS` 不要求系统已有异常证据，但必须明确标注为 AI 风险分析，不能伪装为系统事实。它不是 Risk、MutationDraft 或 Interaction。
11. Risk 支持 1:N Todo；Risk 创建继续产生一个默认 Todo，且一个 Risk 最多一个系统默认 Todo。
12. Agent 创建 Risk 的 `sourceType=AGENT`；reporter 是点击确认的真实用户。
13. 批量 Risk 创建采用 partial success；每个单独 Risk 的 Risk + 默认 Todo + timeline + audit 仍是原子事务。
14. Provider/model、权限、scope、资源状态、分类与状态转换在真正 commit 前重新校验，不信任 draft 时快照。
15. PostgreSQL 是唯一业务与 durable 状态 authority；Redis 仅作 Celery transport。
16. 保持现有 conversation API 与 `/api` envelope、Cookie、错误、时间、分页和权限兼容；所有契约变化必须经 OpenAPI 检查。
17. 日志、audit、error、SSE 与 Provider call log 不得泄露 key、完整 prompt、完整 Tool Result、完整 Provider response 或大量业务正文。
18. 不建立 NestJS/FastAPI 双写、双 migration 或 runtime 依赖。

## 6. 跨 Task 契约冻结点

- Task 1 冻结 `AiProviderAdapter`、Provider Account/Model Config、候选模型快照和错误分类边界；Task 2 只能消费，不把厂商逻辑拉回 Core。
- Task 2 冻结 read-tool catalogue、native loop、grounding/provenance 与 execution error contract；Task 3 只扩展暂停/恢复。
- Task 3 冻结 `AgentInteraction`、`WAITING_FOR_USER`、`interaction.required/resolved` 和 respond API；Task 4 复用它承载写确认。
- Task 4 冻结 mutation draft/commit、partial success 与领域规则；Task 5 不新增后端业务语义。
- Task 5 只做消费者接线、兼容收口、删除有替代且零引用的旧路径和全量验收，不能成为遗留后端功能垃圾桶。

## 7. ADR reconciliation gate

需求与既有批准 ADR 存在实质差异，不能静默融合。开始相应功能代码前，必须形成并批准 V2 ADR 或对现有 ADR 的明确 addendum，至少覆盖：

| 既有 authority | 差异 | 最晚门禁 |
|---|---|---|
| ADR 0005 | 通用 OpenAI-compatible/public-private Provider 与 V1 DeepSeek-only、Account/Model 分层不同 | Task 1 编码前 |
| ADR 0019 | confirmation token 空 body API 与统一 editable `AgentInteraction/respond` 不同；SSE 事件集也需扩展 | Task 3 编码前 |
| ADR 0020 | 仅 `REPORT/PROCESS/RESOLVE` 与 V2 六类 mutation、Risk 1:N Todo、Project status command 不同 | Task 4 编码前 |
| ADR 0028/0029 | 固定两轮内部 JSON V2 与 native Tool Loop、模型候选 failover、proposal 模型不同 | Task 2 编码前；Task 4 补齐 mutation 部分 |
| ADR 0015/现有 schema baseline | 核心 Todo 唯一关系和 Risk source enum 需要 additive migration | Task 4 migration 前 |

这些是已知 `DESIGN_DEVIATION` gate，不是让实现者猜测的开放授权。Task 1 开始时若尚无批准的 Provider V2 ADR，必须将 Task 1 标记 `BLOCKED` 并停止代码实现。后续 Task 同理。

## 7.1 Activation / scheduling gate

仓库 `AGENTS.md` 规定 implementation 必须由 `docs/implementation/TASK_GRAPH.md` 与 assigned `Txxx` 驱动。本轮严格只建设规划文档，因此不修改 Task Graph、Task 状态或 execution state。

开始 Task 1 前，Orchestrator 必须完成一次正式 activation：

1. 把本计划 5 个主 Task 一一登记或映射为恰好 5 个 `Txxx`，保持本文件的名称、边界和线性依赖；不得拆成十几个 implementation Task。
2. 为每个 `Txxx` 补齐现有流程要求的 authority refs、read/write set、acceptance、review/report 路径和状态。
3. 将当前唯一 assigned Task 设为 Task 1 对应的 `Txxx`；其余保持未开始。
4. activation 只做调度/元数据，不顺带实施功能。

激活后状态同步规则：`TASK_GRAPH.md`、assigned `Txxx`、`EXECUTION_STATE.md` 和正式 Task report 继续作为正式调度和机器可读状态 authority；本目录 task `# Status`、`progress.md` 与 README current task 作为 V2 详细交接镜像，由 Orchestrator 在 review/checkpoint 后同步。Implementer 不得只改本目录来领取、完成或推进 Task，也不得自行修改 Task Graph/execution state。若状态不一致，记录 repository state conflict 并停止，按正式 execution state 由 Orchestrator 修复。

若 Task Graph 尚未登记，任何实现 Agent 都必须报告 `BLOCKED` 并停止。若登记过程需要改变本计划的产品边界或 5 Task 数量，则不是普通调度，必须重新获得用户批准。

## 8. Database 与 migration 策略

项目尚未上线，不设计 online migration、dual write、production backfill 或 legacy production data conversion。仍必须保证：

- fresh PostgreSQL database 可沿单一 Alembic chain 升级到 head；
- migration 可在 PostgreSQL 上验证 enum、FK、unique/partial unique、transaction 与 locking；
- 旧 `ai_provider_configs` 数据保留，不自动转换为 `DEEPSEEK_OFFICIAL`；
- V2 runtime 只读新的 DeepSeek Provider Account/Model Config；旧表在 Task 5 仅在引用审计证明安全时清理，否则明确保留为 legacy；
- 不建立第二套数据库或 migration authority。

## 9. 每 Task 完成协议

每个 Task 只有同时满足以下条件才可标记 `COMPLETED`：

1. 所有 Acceptance Criteria 客观 PASS。
2. Task 指定测试和项目级受影响 gates PASS。
3. 独立 Reviewer 给出 `REVIEW_PASSED`，且 findings 已关闭。
4. API/OpenAPI、migration、配置模板与文档一致。
5. 无越界文件修改；无 secret、业务数据或 Provider 原文进入 Git。
6. 创建 checkpoint commit，并在 `progress.md` 记录 started/completed、SHA、摘要、架构决定、DB/API 变化、tests、Review、已知限制与 deferred work。
7. README 指向下一 Task，然后停止。
