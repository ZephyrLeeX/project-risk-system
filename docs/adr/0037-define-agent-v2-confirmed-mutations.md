# 定义 AI Agent V2 confirmed mutations 与 MutationDraft

状态：已批准（T051 用户指令于 2026-08-17 明确批准）

## Decision

### Closed model catalogue

模型只可看到以下六个 proposal tools：

`risk_create_proposal`、`risk_update_proposal`、`risk_resolve_proposal`、
`todo_create_proposal`、`todo_update_proposal`、`project_status_update_proposal`。

proposal 只做严格 schema/domain pre-validation，并持久化 `MutationDraft` 与
`WRITE_CONFIRMATION` interaction。模型 tool registry 永远不包含 commit handler；commit
handler 只能由服务端 interaction service 调用。

### Draft and confirmation

`MutationDraft` 保存 operation、owner/conversation/execution、project/resource IDs、
validated proposal fields、CandidateRisk provenance references、digest、version、
idempotency key 和状态。`AgentInteraction` 扩展 `WRITE_CONFIRMATION`，响应 action 仅为
`CONFIRM` 或 `CANCEL`，确认可以提交 operation allowlist 内编辑后的最终字段。

确认在同一个 PostgreSQL transaction 中锁定并一次性消费 interaction/draft；消费前重新
strict validate、RBAC、project data scope、resource/category/status/business state。owner、
conversation、digest/version、expiry、replay/double-click/idempotency 任何一项不满足都
fail closed；没有确认绝不写入业务表。确认动作和 Domain mutation 分别使用 metadata-only
Agent audit 与现有 Domain audit，均不得写入 draft 正文、prompt、Tool Result 或 Provider
response。

### Risk and Todo

- Agent-created Risk 使用 `RiskSourceType.AGENT`，reporter 永远是实际确认用户。
- Risk update allowlist 仅为 `title`、`description`、`level`、`category`、`evidence`、
  `suggestion`；不得编辑 project/source/reporter/createdAt。
- Risk create/update/resolve 必须调用现有 Risk Domain Service；create 与默认 Todo、timeline、
  audit 为一个单项事务。
- 一个 Risk 可有多个 Todo；Risk create 仍创建一个 `isDefaultForRisk=true` 的默认 Todo。
  `todo_create_proposal` 只能绑定已有 Risk，不允许普通独立 Agent Todo。数据库 partial
  unique invariant 与 Domain pre-check 双重保证每 Risk 至多一个 default Todo。

### Batch and provenance

批量 Risk proposal 可编辑、可选择/取消。commit 对每个选中 item 使用独立事务，返回
per-item success/failure；A 成功、B 失败、C 成功时 A/C 保留。每单项使用幂等键并保持
自身原子性，不提供整体回滚。

CandidateRisk 的 basisType 为 `SYSTEM_FACT`、`AI_ANALYSIS` 或 `MIXED`。AI_ANALYSIS 不
要求系统异常证据，但必须明确标注 AI 分析，且不得伪装系统事实；SYSTEM_FACT/MIXED 的
provenance 只能引用当前 execution 中当前用户真实授权、成功且 immutable 的 Tool invocation。

### Project status

Project status 只允许现有 `DELIVERY`、`COMPLETED`、`ARCHIVED`。合法 transition 必须由
集中 Project Domain Policy/Service 决定，proposal/commit 不得复制规则或猜测 transition。
当前 authority 审计未发现该 policy；因此 `project_status_update_proposal` 在 policy 批准
前保持 `DESIGN_GAP`/blocked，不产生 draft 或业务写入。补齐 policy 必须另行批准，不属于
本 addendum 的推断实现。

### Database

只新增 Alembic chain：`RiskSourceType.AGENT`、action_items 的 default marker 与
`riskId` 非唯一关系、MutationDraft/interaction confirmation/result 所需持久化。部署按
fresh PostgreSQL + Alembic chain 验证；不做 production backfill、dual-write 或 online migration。

