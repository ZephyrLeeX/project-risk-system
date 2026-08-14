# Wave 20 Integration Report

## 结果

- Wave 20 Integration：`PASS`
- T033（admin overview cutover）：`REVIEW_PASSED`（code checkpoint `d8dfcec2b89db7205521b48bd3ae0cf275d1a4e3`）
- T034（weekly reports + Agent cutover）：`REVIEW_PASSED`（code checkpoint `2a232d43f39fdfdf29a4f315bbbd9ca108a5fa78`；metadata checkpoint `d90d825fd9fd44a89e69f6ae6ff44a00b539e632`）
- OpenAPI authority：FastAPI（93 paths / 243 schemas / 104 operations）——未修改、未 re-freeze
- frontend 唯一 API contract source：generated `openapi.ts`（20 个 `OpenApi.components["schemas"][...]` 别名，无手写重复契约）
- frozen OpenAPI export / generation：zero diff（`pnpm contracts:check` clean-tree exit 0）
- integration fix：无
- Browser E2E：unavailable（仓库无 Playwright/Cypress harness，仅有 vitest unit 测试）——按事实记录，未伪报为 PASS
- 下一 Wave（Wave 21 = T035）：未启动（仅同步 readiness）
- DG-05 / DG-08：未处理

## 范围

Wave 20 为双工作单元 Wave，授权 T033（admin pages cutover）与 T034（dashboard / weekly reports / mailbox
/ Agent UI cutover），二者 write-set 不相交（disjoint page/API modules），且均只消费、不编辑 generated 类型。
T033、T034 均已在各自执行单元完成 `REVIEW_PASSED`。本 Integration 在二者 `REVIEW_PASSED` + checkpoint 之后执行
联合验证，确认 T033/T034 前端与 live FastAPI `/api` contract 联合一致，且未引入回退。

T033/T034 为纯前端 cutover（write-set 仅 `apps/web/src/` 下文件），未触及 backend、frozen OpenAPI authority、
migration 或测试基线。`contracts:check` 期间 production FastAPI `app`（T040 composition）正常 boot 并导出
OpenAPI，是其 live contract 可用性的直接证据。

## 契约联合一致验证

### 1. T033 admin overview UI ↔ live FastAPI `/api/admin/overview`

| 前端调用（`apps/web/src/api/admin.ts`） | live FastAPI 路由 | 生成类型（`OpenApi.components["schemas"]`） |
| --- | --- | --- |
| `adminApi.overview()` → `GET /admin/overview` | `admin/overview/api.py:25` `@router.get("/overview", response_model=ApiResponse[AdminOverview])` | `AdminOverview` |
| `adminApi.projects()` → `GET /admin/projects/options`（T044 surface） | `admin/options/api.py:35` `@router.get("/projects/options", ...)` | `ProjectOption[]` |

- `admin.ts` 以 6 个生成类型别名（`AdminOverview`/`HealthItem`/`AttentionItem`/`RecentAuditItem`/
  `UnavailableSection`/`OverviewLink`）re-reference frozen `openapi.ts` authority，无手写替代契约。
- frozen authority 中这些 schema 字段为真实类型（`AttentionItem.kind`=`enum`、`link`=`$ref OverviewLink`、
  `occurredAt`=`{date-time,string}`；`HealthItem.key`/`status`=`enum`；`AdminOverview.generatedAt`=
  `{date-time,string}`），无 `unknown` 退化（T045 fidelity）。
- `AdminDashboardView.vue` 三段（health / attention / recentAudit）由固定业务数组改为 `v-for` 消费
  `AdminOverview` 真实字段；`navigateLink(link)` 以 `router.push({ path, query: { ...link.query } })`
  透传契约 path + query，不硬编码；health item `link=null` 时 no-op。
- loading / empty / error / unavailable：各段 v-if/v-else-if 链覆盖「未加载」「`section=null` 且
  `unavailableSections` 命中（FORBIDDEN/TIMEOUT/DEPENDENCY_FAILURE）」「空数组」「有数据」；顶部 fetch error
  渲染 `role="alert"` 错误条 + 重试。
- dynamic health rollup：`overallHealthStatus` 由 item 状态聚合（UNAVAILABLE 主导 DEGRADED，空数组显式
  EMPTY），badge tone 动态。
- `admin-overview.test.ts` 7 个 vitest 覆盖 health rollup（含 UNAVAILABLE 主导、EMPTY）、per-item label/glyph、
  attention 严重度、audit result label、3 个 unavailable reason label、`findUnavailable` 命中/未命中。

### 2. T034 weekly reports UI ↔ live FastAPI weekly endpoints

| 前端调用（`apps/web/src/api/weekly-reports.ts`） | live FastAPI 路由 | 生成类型 |
| --- | --- | --- |
| `weeklyReportsApi.current()` → `GET /weekly-reports/current` | `weekly_reports/api.py:28` | `WeeklyReportResponse` |
| `weeklyReportsApi.report(weekStart)` → `GET /weekly-reports/{week_start}` | `weekly_reports/api.py:37` | `WeeklyReportResponse` |
| `weeklyReportsApi.detail(weekStart, projectId)` → `GET /weekly-reports/{week_start}/projects/{project_id}` | `weekly_reports/api.py:47` | `WeeklyProjectDetail` |

- `weekly-reports.ts` 以 5 个生成类型别名（`WeeklyReportResponse`/`WeeklyProjectSummary`/
  `WeeklyProjectDetail`/`WeeklyReportItemResponse`/`WeeklyProject`）re-reference frozen authority。
- frozen authority 中 `WeeklyReportResponse.projects`=`$ref array`、`freshnessDeadline`/`generatedAt`=
  `{date-time,string}`；`WeeklyProjectDetail.items`=`$ref array`、`project`=`$ref`，真实 item 级字段类型。
- `DashboardView.vue` 周报面板消费 `weeklyReportsApi.current()` 真实 `WeeklyReportResponse`：header 显示
  week 范围 + reportCount/riskCount 汇总；表体为 `projects[]`，点击有风险项目打开 `WeeklyProjectDetail`
  明细 modal，明细消费 `items[]`（summary/riskLevel/riskStatus/todoStatus/occurredAt）。
- loading / empty / stale / error：周报面板 loading / error（含 503 `WEEKLY_REPORT_STALE` 经由 error 分支 +
  重试）/ empty / data（stale 内联标记）；无静态周报数组残留（grep `weeklyReports = [` / `sendAgent` 无命中）。
- `weekly-reports.test.ts` 8 个 vitest 覆盖 summary/level 计数窄化、等级分布行、week 范围、stale 标签、
  risk/todo 状态标签、`projectHasRisks`。

### 3. T034 Agent UI / SSE ↔ live FastAPI agent endpoints

| 前端调用（`apps/web/src/api/agent.ts`） | live FastAPI 路由 | 生成类型 |
| --- | --- | --- |
| `agentApi.help()` → `GET /agent/help` | `agent/api.py:51`（`agent.use`） | `AgentHelpResponse` |
| `agentApi.create(message)` → `POST /agent/conversations` | `agent/api.py:60`（201，`agent.use`） | `AgentConversationEnvelope` |
| `agentApi.continueConversation(id, message)` → `POST /agent/conversations/{id}/messages` | `agent/api.py:74`（202，`agent.use`） | `AgentMessageEnvelope` |
| `agentApi.history(id)` → `GET /agent/conversations/{id}` | `agent/api.py:92`（`agent.use`） | `AgentConversationHistory` |
| `agentApi.messages(id, {afterSequence, limit})` → `GET /agent/conversations/{id}/messages` | `agent/api.py:105`（`afterSequence`/`limit` query，`agent.use`） | `AgentMessagePage` |
| `agentApi.confirm(token)` → `POST /agent/confirmations/{token}`（空 body `{}`） | `agent/api.py:145`（`agent.use`，`del payload`） | `AgentConfirmationResponse` |
| SSE stream → `GET /agent/conversations/{id}/events`（`after` query） | `agent/api.py:125`（`after: UUID \| None` Query，`text/event-stream`，`agent.use`） | wire payload `unknown` + 运行时守卫 |

- `agent.ts` 以 9 个生成类型别名 re-reference frozen authority；SSE stream 在 OpenAPI 中为
  `text/event-stream: unknown`，故以运行时类型守卫窄化 wire payload，全程无 `any`。
- create / continue：`useAgentConversation.send()` 按 `state.conversationId` 分支 create vs continue；
  continue 路径不访问 `.conversation`（envelope 形状差异已正确处理）。
- SSE parser / reducer：`utils/agent-sse.ts` 纯 `parseSseFrames`（`\n\n` 边界、CRLF 归一、多行 data、
  跨 chunk carry）+ `applyFrame`（`message.delta`/`progress`/`preview`/`completed`/`error`/`heartbeat`）；
  `agent-sse.test.ts` 15 个 vitest 覆盖 parser/reducer/resume-safety/错误标签/operation 标签/preview 摘要。
- reconnect `after=lastEventId`：`reconnect()` → `connectStream(activeStreamUrl, state.lastEventId)` →
  `withResumeCursor` 追加 `after=<lastEventId>`，与后端 `events` 的 `after: UUID | None` query 对齐
  （ADR 0019 reconnect cursor）；stream 非正常关闭且未见 terminal event 时 `markDisconnected()` 提供重连。
- provider-failure retry：`retry()` 重发 `lastUserMessage`；`applyRequestError` 对 `status >= 500 || status === 0`
  标记 `retryable`；stream error `AGENT_EVENT_CURSOR_UNRECOVERABLE` 不可重试，其余可重试。
- preview one-use confirmation：`confirmPreview()` 以空 `{}` body 调 `agentApi.confirm(token)`（token 唯一
  授权 canonical preview content，ADR 0029）；成功写入 `preview.result`，失败置 `failed`。
- confirmation error handling：失败时 `preview.failureMessage = confirmationErrorLabel(code, fallback)`
  映射 6 个 confirmation 错误码到中文标签。
- stream read-only semantics：`connectStream` 以 `fetch` GET 消费 `response.body` reader，仅 `applyFrame`
  归约到 view state，不在 stream 上发起任何写操作。
- `agent.use` permission gate：`canUseAgent = computed(() => Boolean(auth.user?.permissions.includes("agent.use")))`，
  Agent 入口按钮 `v-if="canUseAgent"`；后端全部 agent 路由 `Depends(require_permissions("agent.use"))`。

### 4. T043 mailbox FastAPI surface ↔ T034 UI 无回退

- `apps/web/src/api/mailbox.ts` 保留全部 T043 sync-results 浏览/重试端点：`/mailbox/sync-summary`、
  `/review-options`、`/messages`、`/messages/{id}`、`POST /messages/{id}/retry`、`/sync-batches`，与
  `mailbox/sync_results/api.py` 路由一一对应；T034 cutover 未触碰 mailbox UI / API。
- `DashboardView.vue` mailbox 同步链接保留 `mailbox.sync_self` 守卫，未回归。

### 5. generated `openapi.ts` 为 frontend 唯一 API contract source

- `admin.ts` / `weekly-reports.ts` / `agent.ts` 共 20 处 `OpenApi.components["schemas"][...]` 别名，全部
  re-reference frozen generated authority；无手写重复 contract 类型、无 `any` cast 覆盖生成类型。
- frontend 不再依赖本地 fake Agent responses 或 static weekly-report business data：`DashboardView.vue`
  消费 `weeklyReportsApi.current()/detail()` + `useAgentConversation` + `agentApi.help()`；grep 确认无
  `sendAgent` 伪造应答分支、无 `weeklyReports = [` 静态数组残留。

## 验证

| # | 检查 | 命令 | 结果 |
| --- | --- | --- | --- |
| 1 | T033 admin overview UI ↔ live FastAPI contract | 契约逐对比对 + frozen schema 真实类型 | PASS |
| 2 | T034 weekly reports UI ↔ live FastAPI contract | 契约逐对比对 + frozen schema 真实类型 | PASS |
| 3 | T034 agent UI/SSE ↔ live FastAPI contract | 契约逐对比对 + composable/reducer 行为核查 | PASS |
| 4 | T043 mailbox surface 无回退 | `mailbox.ts` ↔ `sync_results/api.py` 端点比对 | PASS |
| 5 | generated `openapi.ts` 单一 contract source | 20 处 `OpenApi.components["schemas"]` 别名，无手写重复 | PASS |
| 6 | frontend 无 fake Agent / static weekly 数据 | `DashboardView` 消费真实 API；grep 无残留 | PASS |
| 7 | `pnpm contracts:check` clean-tree | `contracts:sync` + `git diff --exit-code` | exit 0（zero diff） |
| 8 | frozen OpenAPI export/generation zero diff | `contracts:check` 运行后 `git status --porcelain` 为空 | PASS |
| 9 | `@risk-platform/contracts` typecheck | `tsc -p tsconfig.json --noEmit` | exit 0 |
| 10 | `@risk-platform/web` typecheck | `vue-tsc -b --noEmit` | exit 0 |
| 11 | web tests | `vitest run` | 4 files / 33 tests PASS |
| 12 | web production build | `vue-tsc -b && vite build` | PASS（built in 1.36s） |
| 13 | `git diff --check` | — | clean |
| 14 | Browser E2E | — | unavailable（无 harness，按事实记录） |

### frozen OpenAPI authority

- T033/T034 为纯前端 cutover，未修改 backend、未 re-freeze OpenAPI authority。
- `contracts:check` 以当前 HEAD（含 T033/T034）的 production `app` 导出 OpenAPI，与 tracked
  `packages/contracts/openapi/openapi.json`（93 paths / 243 schemas / 104 operations）逐字节一致，
  `openapi.ts` reproducibly 生成 zero diff。authority 流向 FastAPI → `openapi.json` → `openapi.ts`，
  无 NestJS/Prisma runtime、无双写。

### web tests 明细

```
src/utils/weekly-reports.test.ts   (8 tests)
src/utils/agent-sse.test.ts        (15 tests)
src/utils/admin-overview.test.ts   (7 tests)
src/views/EngineeringBaselineView.test.ts (3 tests)
Test Files  4 passed (4)
     Tests  33 passed (33)
```

### Skipped / unavailable validation

- **Browser E2E**：unavailable。仓库无 Playwright/Cypress E2E harness（仅 vitest unit 测试）。
  T033/T034 view acceptance（admin overview loading/empty/error/unavailable、weekly current/detail/stale、
  agent create/continue/reconnect/retry/preview/confirm）由生产 build（vue-tsc 模板编译 + vite build）+
  驱动视图状态的 presentation/reducer 纯 unit 测试覆盖，与仓库既有 validation 范围一致（同 T033/T034 report）。
  未将不存在的 E2E 伪报为 PASS。
- **backend Ruff / mypy / full pytest / `uv lock --check`**：not applicable。T033/T034 write-set 为纯前端，
  未触及 Python/lock write-set；`contracts:check` 已 boot production FastAPI `app` 并导出 OpenAPI，
  证明 live backend contract 可用且与 frozen authority 一致。backend 全量回归已于 Wave 19（同一 backend
  HEAD lineage，`283 passed, 1 skipped`）建立，本轮无 backend 变更，不重复执行。

## Integration fixes

无。T033/T034 frontend cutover 未暴露 integration failure：前端消费的 generated `openapi.ts` 类型与 live
FastAPI export byte-identical 的 frozen authority 一致，契约逐对比对全部对齐，mailbox 无回退。未修改任何
production 代码、测试基线、migration 或 contract artifact，未修改 backend 或 frozen OpenAPI authority。

## Checkpoint

Wave 20 final checkpoint：见 `EXECUTION_STATE.md`（本次 WAVE-20 report + 状态更新提交后记录）。

## Next-wave readiness

仅同步下一任务 readiness，不执行下一 Task、不启动下一 Wave。

- **T035**（Define production Compose, Python processes, proxy, secrets and persistence after final
  backend/frontend composition；deps T031–T034、T040）：T031 / T032 / T040 均 `REVIEW_PASSED`，T033 / T034
  `REVIEW_PASSED` + Wave 20 Integration `PASS`，direct dependencies 全部满足。T035 可在 Wave 21 评估/执行。
- Wave 21（T035）未启动。
- DG-05 / DG-08 保持 out of scope。
