# Task 4 — 写操作 + 人工确认 + Risk/Todo/Project Mutation

# Status

`NOT_STARTED`

# Goal

在模型只能生成 `MutationDraft`、用户必须显式确认、服务端 commit handler 独占真实写权限的边界下，实现 Risk/Todo/Project 全部批准 mutation，并落地 Risk 1:N Todo、CandidateRisk grounding 和批量 Risk partial success。

# Prerequisites

- Task 3 为 `COMPLETED` 且 `REVIEW_PASSED`，checkpoint 已核对。
- 完整阅读需求 §§21–36、37、41–42、46–50 与本计划/进度/Task 文件。
- 阅读 ADR 0004、0008、0013、0015、0017、0019、0020、0029、当前 Risk/Todo/Project/timeline/audit services 与 Prisma/NestJS reference。
- 批准的 V2 mutation ADR/addendum 已冻结六类 proposal、editable confirmation、批量 partial success、Risk 1:N Todo/default marker、audit、Project status policy。
- 若现有 authority 无法确定 Project 合法状态转换，报告 `DESIGN_GAP` 并停止 project mutation 部分；不得自行发明。

# Scope

- 模型 catalogue 只新增 proposal tools：`risk_create_proposal`、`risk_update_proposal`、`risk_resolve_proposal`、`todo_create_proposal`、`todo_update_proposal`、`project_status_update_proposal`。
- proposal tools 只验证并持久化 `MutationDraft`，创建 `WRITE_CONFIRMATION` interaction；真正 commit handler 永不进入 model catalogue。
- respond API 支持 `CONFIRM`/`CANCEL`，确认可提交用户编辑后的最终批准字段；服务端 strict validation 后重新计算绑定。
- Risk create/update/resolve；Todo create/update；Project status update 全部复用/扩展 Domain Service/Policy，不在 Agent 复制规则。
- CandidateRisk 每项包含 title、description、level、category、evidenceSummary、source/tool provenance；无系统依据不能确认。
- 批量候选可编辑、取消部分项目并一次确认；commit 采用 per-item partial success，每个 Risk 自身事务原子。
- Agent Risk `sourceType=AGENT`，reporter 为确认用户。
- Risk 创建保留一个系统默认 Todo；Risk 改为 1:N Todo，并由 DB/domain invariant 保证最多一个 default Todo。
- Todo create 仅允许已有 Risk，不允许 Agent 独立 Todo。
- Risk update 禁改 projectId/sourceType/reporter/createdAt；其他字段按需求 allowlist。
- Todo update 和 Risk resolve 复用现有状态、关联 Todo、timeline、audit 语义。
- Project status 只使用现有 `DELIVERY/COMPLETED/ARCHIVED`，合法转换由集中 Domain Policy 决定。
- commit 前重检 RBAC/data scope/resource state/category/status/assignee/business rule；replay/idempotency/concurrency fail closed。
- Agent channel audit 记录确认动作；Domain audit 继续记录实际业务写入。

# Non-goals

- 不给模型真正的 create/update/resolve SQL/ORM/domain commit capability。
- 不允许 SSE 或 `interaction.required` 自动提交写操作。
- 不新增用户未定义的 Risk/Project/Todo 状态。
- 不创建无 Risk Todo，不改变 Risk reporter 为 AI。
- 不实现整体事务式批量回滚；需求固定为 partial success。
- 不修改前端；Task 5 才提供可编辑确认 UI。
- 不清理 V1 confirmation/provider/core；归 Task 5。

# Current implementation impact

- 当前 Agent 只支持 `REPORT/PROCESS/RESOLVE` token confirm，且 request body 必须为空，不能承载用户编辑。
- `RisksService` 已有 create/resolve/reopen；create 会调用 `TodosService.ensure_for_risk`。
- `TodosService` 有 update/process 和 `ensure_for_risk`，但没有通用 risk-bound create；当前 `riskId` unique 强制 1:1。
- `RiskSourceType` 缺少 `AGENT`。
- 当前 Project 模块只有 models，未见集中 status command/service；import rollback 可写状态但不是用户状态转换 authority。
- 现有 audit 是 typed metadata-only append-only contract，不能塞 draft/provider/tool payload。

# Backend

- Proposal schema 与 commit command schema 分离；服务端从确认用户提交构建最终 command，禁止 mass assignment。
- commit handler 只能被 interaction service调用，并要求 resolved-before/atomic consume/idempotency contract。
- Risk create 单项 transaction 包含 Risk、default Todo、timeline、Domain audit、Agent confirmation audit/result；任何一步失败整项回滚。
- 批量 handler 对选中 draft 逐项开启独立 transaction，返回每项 success/failure code，不泄露内部异常；失败 B 不回滚 A/C。
- `sourceInvocationIds` 必须指向同 conversation/execution、成功、授权查询的 immutable provenance；证据摘要与来源关系在 commit 前验证。
- Domain services 提供 Agent 与普通 API 可共同使用的明确 command methods；权限策略不复制。

# Frontend

本 Task 不修改前端。API/SSE fixture 必须能独立验证编辑后的 single/batch confirm、cancel、partial results 和 replay。Task 5 只能展示和调用这些已冻结 contract，不能在浏览器实现 business validation。

# Database impact

- `RiskSourceType` 增加 `AGENT`。
- 移除 `action_items.riskId` 全局唯一约束，新增 default marker（具体字段由 ADR 固定）及 `WHERE riskId IS NOT NULL AND isDefaultForRisk` 的 partial unique invariant；普通 risk-bound Todo 可多条。
- 新增/扩展 MutationDraft、interaction confirmation/result、batch item/provenance/idempotency 持久化。
- migration 面向 fresh database + 正常 Alembic chain；无 online migration、dual write 或 legacy production backfill。
- 必须验证 PostgreSQL enum、partial unique、FK、transaction、row lock 与 concurrent confirm。

# API / Contract impact

- 扩展 Task 3 interaction respond request 以支持 `CONFIRM`/`CANCEL` 和按 operation allowlist 的 editable final fields。
- `interaction.required` 对 WRITE_CONFIRMATION 提供 UI 必要 draft、evidence summary、可编辑字段和批量 item IDs；不暴露内部 Tool Result。
- `interaction.resolved`/respond response 提供 single 或 per-item partial success 结果。
- 原 conversation API 保持兼容；旧 `/agent/confirmations/{token}` 的退役策略由批准 ADR 冻结并在 Task 5 完成消费者切换后执行。
- Risk/Todo public contract 中 `AGENT` enum 和 1:N 关系的可见字段变化必须更新 OpenAPI/generated types；不得改变既有 envelope。

# Security requirements

- `NO CONFIRMATION = NO MUTATION` 必须由架构与 negative tests 证明。
- commit handler 无 model registry entry；静态 registry test 必须断言。
- interaction owner、conversation、draft digest/version、一次有效与 idempotency；用户可编辑字段之外一律 422/409。
- confirm 时重新检查 permission/scope/resource/category/assignee/status；draft 期间权限变化 fail closed。
- 跨 project risk/todo、已解决 risk、archived project、disabled category 和 stale resource 都有不泄露存在性的错误。
- audit/log 不写 prompt、draft正文、evidence全文、tool result/provider response；允许固定 operation/resource/result/reference metadata。

# Tests

- registry negative：模型只能看到 proposal tools，永远看不到 commit handler。
- 每种 mutation success + validation + permission/scope + stale/concurrent/replay/idempotency + rollback。
- Risk create：`AGENT` source、真实 reporter、一个 default Todo、timeline/audit 同事务。
- Risk 1:N：多个 Todo 可建，第二个 default 被 DB 拒绝；并发 default create 只有一个。
- Risk update immutable fields；resolve 联动开放 todos；Todo create 仅 risk-bound；Todo update allowlist。
- Project status transition matrix来自批准 policy；不允许新 enum/跳过 policy。
- CandidateRisk 无 provenance/跨 execution/stale provenance fail closed；UI summary不需原始 JSON。
- Batch 0/1/N、选择取消、A成功-B业务失败-C成功、每项事务原子、重复 confirm 不重复创建。
- audit typed metadata/append-only、secret/content leakage negative。
- fresh PostgreSQL migration/constraints、OpenAPI/contracts regressions。

# Acceptance Criteria

1. 六个 proposal tools 均只产生 draft/interaction，任何未确认路径数据库业务表零变化。
2. commit handler 不在 model tool catalogue，模型构造任意 tool name也无法调用。
3. 所有六类 mutation 经 editable `WRITE_CONFIRMATION` 成功，并在 commit 时通过 current RBAC/scope/domain revalidation。
4. Risk create 的 source/reporter/default Todo 正确；同 Risk 可拥有多个 Todo但最多一个 default，由 PostgreSQL 与 domain 双重保证。
5. 批量 Risk 严格 partial success；单项 Risk transaction 原子且 replay 不重复。
6. CandidateRisk 缺少真实 Tool provenance 时不可进入 commit。
7. Project status 只走批准 Domain Policy；若 policy authority 缺失，该 capability 保持 blocked，不以猜测通过。
8. audit、OpenAPI、migration、existing non-Agent Risk/Todo behavior 全部回归通过。

# Quality Gates

- uv lock check、Ruff、mypy。
- focused mutation/domain/interaction/audit/PostgreSQL concurrency tests。
- 全量后端 pytest；OpenAPI export/check、contracts check、frontend typecheck。
- Alembic single head/fresh upgrade/schema constraint inspection。
- 静态 tool catalogue/直接 SQL/commit handler exposure 搜索。
- `git diff --check`；变更范围审计。

# Independent Review

Reviewer 必须用攻击者视角验证无确认写入、编辑字段 mass assignment、replay/double-click、scope 撤销、stale category/status、cross-project ID、batch rollback边界、default Todo 并发和 audit 泄露。另需从普通 Risk/Todo API 验证 1:N 变更没有破坏既有生命周期。

# Completion Deliverables

- 批准 mutation ADR/addendum 和 Project status policy reference。
- schema/migration、Domain Service/Policy、proposal/draft/commit implementation。
- six-operation + batch/provenance/audit/security tests。
- OpenAPI/generated contract、implementation report。
- checkpoint SHA 和 README/progress 更新。

# Handoff to Next Task

Task 5 只消费已冻结的 Admin/Agent contracts，完成 UI 与清理。交接必须列出所有 interaction payload/event/error shapes、editable fields、batch result、old endpoints待退役清单、migration head、E2E fixtures和checkpoint。完成后停止，不自动开始 Task 5。
