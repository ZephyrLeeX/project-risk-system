# Task 5 — 前端/Admin 收口 + 旧 Agent 架构清理 + 全量 E2E

# Status

`NOT_STARTED`

# Goal

让现有 Vue/Admin 页面完整消费 V2 OpenAPI contract，覆盖 interaction 与可编辑确认体验；在零引用和回归证据下删除已替代 Agent V1 路径，并以真实 FastAPI/PostgreSQL/Redis/Celery + fake/approved DeepSeek endpoint 完成全量 E2E。

# Prerequisites

- Task 4 为 `COMPLETED` 且 `REVIEW_PASSED`，checkpoint 已核对。
- 完整阅读全部 V2 需求、总体计划、进度、Task 1–4 completion records 和本文件。
- 阅读 ADR 0011、0016、0019、Task 1–4 V2 addenda、当前 Admin Provider view、Agent composable/SSE reducer/Dashboard UI、OpenAPI generator和Compose/proxy。
- 后端 V2 contracts 已冻结；本 Task 不接收新的后端业务语义。

# Scope

- Admin AI 配置切换为 Provider Account + Model Config：DeepSeek-only、模型枚举、健康、default/priority、timeout、key rotation 与错误状态。
- Agent UI 支持 `interaction.required/resolved`、项目单选、以上都不是、手动输入、取消、expired/replay/retry 和 resume stream。
- WRITE_CONFIRMATION UI 展示/编辑 operation allowlist 字段、CandidateRisk 依据、批量选中/取消与 per-item partial success。
- 保持现有 conversation create/continue/history/messages、Markdown、安全渲染、loading/empty/error/disconnected/reconnect 状态。
- 重新导出并冻结 FastAPI OpenAPI，生成 TypeScript types；前端只消费生成 authority，不手写漂移 contract。
- 审计并删除被 V2 完全替代且无引用的内部 JSON protocol、固定 PLAN/RESPOND、旧 Agent provider adapter/fallback、旧 confirmation UI/API/runtime path。
- 保留仍被 mail/weekly 或 legacy data读取所需的 provider code/table；不能为“清理”破坏非 Agent AI 功能。
- Docker Compose/proxy/Celery worker 注册与 SSE buffering/timeout 生产路径验证；不新增服务拓扑。
- 建立/接入可重复 browser E2E harness，覆盖五阶段关键用户旅程。

# Non-goals

- 不新增 Provider、Tool、mutation、状态、权限、领域规则或数据库业务能力。
- 不重做整体视觉设计；沿用现有设计系统。
- 不删除有运行时引用或数据保留要求的 legacy provider table/code。
- 不用 mocked unit test 冒充真实 worker/SSE/PostgreSQL E2E。
- 不启动任何第 6 Task；本 Task 完成即整个 V2 计划结束。

# Current implementation impact

- `ApiKeyManagementView.vue` 当前按单层 Provider 配置展示 vendor/endpoint/protocol/model。
- `useAgentConversation` 当前只管理一个 token preview；`agent-sse` reducer 不认识 interaction events。
- `agentApi.confirm` 当前 POST 空 object 到 `/agent/confirmations/{token}`。
- generated `openapi.ts` 已是前端 authority；SSE payload目前由 runtime guards narrowing。
- 当前仓库没有已识别的 browser E2E harness，需要本 Task 提供可重复方案。
- Compose 已包含 FastAPI、worker、scheduler、Redis、PostgreSQL、frontend、proxy；需验证而非重构拓扑。

# Backend

- 仅允许 integration/composition、旧路径删除、OpenAPI metadata 和为 E2E 暴露的非业务测试 wiring；发现后端功能缺失应退回 Task 1–4 修复并重新 Review，不得在 Task 5 顺手设计。
- 删除前必须证明 replacement path、引用为零、migration/retention安全、mail/weekly无依赖。
- old API retirement 必须符合批准兼容/版本策略；若前端切换不足以授权删除，保留 deprecated path 并记录 limitation。

# Frontend

- UI 不自行判断权限、project match、category有效性、status transition 或 batch transaction；只显示 server contract/result。
- 所有 selectable candidate 用 stable server ID；manual input 文本传 server re-search。
- 确认按钮防 double-submit但不能替代 server idempotency；每项 partial result 可见。
- CandidateRisk 明确展示 evidenceSummary，不展示 raw Tool JSON。
- 处理 required/resolved event replay，不重复弹窗/重复提交；刷新后从 conversation/interaction state恢复。
- Admin 中不出现 Company API 可配置入口，不允许编辑 DeepSeek official endpoint。

# Database impact

默认无新业务 migration。只允许删除已批准且证明安全的 legacy schema（若 V2 ADR明确要求）；否则保留 legacy 表并记录不被 V2 runtime 使用。任何临时发现的新 schema需求视为前序 Task incomplete，停止并回退。

# API / Contract impact

- FastAPI OpenAPI 是最终 authority；export/generate 连续多轮零 diff。
- compatibility diff 明确列出 additive、deprecated、removed surfaces；未批准 breaking diff 为 blocker。
- 前端所有 Agent/Admin V2 request/response 使用 generated types；SSE runtime guards 与批准 event schema逐字段对齐。
- 旧 confirmation endpoint只有在批准 retirement contract、零消费者、后端测试覆盖替代路径后删除。

# Security requirements

- 前端不接收/显示 key、Authorization、raw prompt/tool/provider payload；Admin key 仅写入、遮罩回显。
- Markdown/链接安全、防 HTML/script 注入保持回归。
- UI 隐藏不是授权；E2E 必须验证 server端 403/404/scope。
- SSE reconnect、interaction replay、double confirm、CSRF/cookie/origin 与 trace error shape 覆盖。
- production logs、browser console/network fixtures 不落真实 secret 或敏感业务正文。

# Tests

- Frontend unit/component：Provider Account/Model、model refresh、interaction reducer、project selection/manual、editable confirm、batch partial result、error/replay/reconnect。
- generated contract typecheck、production build、existing UI regressions。
- browser E2E：out-of-scope；read-only query/tool loop；项目全名/别名/简称/地域语义且候选严格来自授权 Tool；跨项目比较/排序和有依据/无依据主动风险发现；project disambiguation+refresh+resume；single mutation confirm/cancel；batch Risk partial success；permission/scope撤销；provider failover；SSE disconnect/reconnect。
- E2E runtime 使用真实 FastAPI/PostgreSQL/Redis/Celery worker和迁移 head；Provider 可用安全 fake DeepSeek server验证 wire contract，真实外部 DeepSeek验收若无凭据必须单列 `BLOCKED_EXTERNAL_INPUTS`，不可伪造成功。
- non-Agent mail/weekly/provider regression、full backend/frontend suite。
- dead-code/reference search、old protocol/endpoint registry absence、container smoke、proxy SSE buffering。

# Acceptance Criteria

1. Admin 可完整管理 DeepSeek Provider Account/Models，且 UI 无 Company API/任意 DeepSeek endpoint入口。
2. Agent UI 可恢复地完成项目消歧、manual input、single/batch editable confirmation、cancel、partial results和replay错误。
3. OpenAPI export/generation重复三轮零 diff，前端无手写 V2 HTTP DTO漂移。
4. 全量 E2E 在真实 PostgreSQL/Redis/Celery/FastAPI/SSE路径通过；外部凭据缺失被诚实单列。
5. 旧内部 JSON protocol/fixed PLAN-RESPOND/confirmation path仅在零引用、替代和兼容证据齐全时删除；mail/weekly无回归。
6. Task 5 diff 不含新增业务语义或意外 migration；若发现缺失已按前序 Task回退并Review。
7. security/permission/scope/secret/Markdown/CSRF/SSE negative tests全部通过。
8. 全部 5 个 Task completion record和final checkpoint完整，新会话无需聊天即可审计结果。

# Quality Gates

- 后端：uv lock check、Ruff、mypy、full pytest、Alembic head/fresh database、OpenAPI export/check。
- 前端：`pnpm contracts:check`、typecheck、unit/component tests、production build。
- E2E harness 与真实 Compose integration suite。
- OpenAPI export/generate 三轮零 diff；compatibility diff review。
- `rg` 搜索旧 protocol/adapter/confirm references并人工归类；`git diff --check`。
- secret scan、container health、worker task discovery、SSE proxy smoke。

# Independent Review

Reviewer 必须独立运行 contract/build/E2E关键 gates，逐项核对 UI不是业务 authority、旧路径删除证据、mail/weekly回归、外部验收诚实性和Task 5无新业务垃圾。Review 还需从全新会话按 README 恢复并确认仅靠仓库可判断全部状态。

# Completion Deliverables

- Provider Admin/Agent interaction/confirmation UI 与 tests。
- 最终 OpenAPI、generated TypeScript、compatibility report。
- old architecture deletion/retention inventory与零引用证据。
- E2E harness、commands、results和外部验收状态。
- Task 5 implementation/review report、final checkpoint SHA。
- README 改为 `ALL_TASKS_COMPLETED`，progress 补齐五个 completion records 和总体 `COMPLETED`。

# Handoff to Next Task

无下一 Task。完成后停止并汇报 AI Agent V2 全部 5 Task 已完成；任何新需求必须另建计划，不得隐式扩展本计划。
