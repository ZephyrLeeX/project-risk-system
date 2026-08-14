# Wave 17 Integration Report

## 结果

- Wave 17 Integration：`PASS`
- T043：`REVIEW_PASSED`（code checkpoint `eb7c9243b170feda08776912c9dcde932ca90114`）
- T044：`REVIEW_PASSED`（code checkpoint `1dc067a44a3e5bcf5e4ecc5305e2060f713ecbd5`）
- T032：blocker 已解除，状态同步为 `READY`（仅 metadata，未执行 T032；candidate 文件保持 uncommitted、未触碰）
- 下一 Wave（Wave 18 / T032）：未启动
- DG-05 / DG-08：未处理

## 范围

Wave 17 为 T032 `DESIGN_GAP` resolution 拆分出的 migration-coverage remediation Wave，
包含两个 disjoint write-set 工作单元：

- **T043**（mailbox sync-results browse/retry surface）：新增 disjoint 子包
  `mailbox/sync_results/`，迁移 NestJS `mail-sync-results` 的浏览/重试端点至 FastAPI。
- **T044**（admin project-options selector）：迁移 NestJS `admin.controller.ts`
  `@Get("projects/options")` 至 FastAPI，对 T012 既有 `admin/options` 模块 additive-only。

二者均为 T032 OpenAPI compatibility 缺口的 remediation owner：T032 candidate 的
compatibility 检查曾暴露 7 个活跃前端消费但 FastAPI authority 缺失的 `/api` 端点
（1 个 admin project-options + 6 个 mailbox sync-results）。本 Wave Integration 验证
T043/T044 `REVIEW_PASSED` 后这些端点已全部进入 FastAPI surface 且契约一致，
T032 freeze 的 blocking 前置已消除。

本 Wave 不修改任何 production 代码、测试基线或 migration；不提交、修改或丢弃 T032
candidate 文件（`openapi_export.py`、`test_openapi_export.py`、`packages/contracts/openapi/`、
`packages/contracts/scripts/`、`packages/contracts/src/generated/` 及对 `pyproject.toml` /
`package.json` / `packages/contracts/package.json` / `src/index.ts` / `pnpm-lock.yaml` 的
uncommitted 修改均保持原样）。

## 环境

复用 Wave 16 建立的 integration 环境：

| 服务 | 镜像 / 版本 | 状态 |
| --- | --- | --- |
| PostgreSQL | `postgres:16-alpine`（PostgreSQL 16.14） | healthy，监听 `localhost:5432`，`risk_test` 库可用 |
| Redis | `redis:7-alpine`（Redis 7.4.10） | `PONG`，监听 `localhost:6379`，作为 Celery broker |

环境变量：`TEST_DATABASE_URL` / `DATABASE_URL` 指向 `risk_test`，
`CELERY_BROKER_URL=redis://localhost:6379/0`，`DATA_ENCRYPTION_KEY` /
`SESSION_SECRET_FILE` / `IMPORT_STORAGE_DIR` 按 `.env.example` 配置。未修改
`infra/docker-compose.yml`（T035 写集范围内）。

## 验证

### 1. 7 个缺失 frontend-live-consumed API 已进入 FastAPI surface

经 `risk_platform.openapi_export.build_openapi()` 对 live production app（`main:app`）
导出，当前 surface 共 93 个路径（Wave 16 为 85，+8 来自 T043/T044）。7 个前端活跃消费
端点全部在册且 (path, method) 唯一：

| 端点 | 来源 | 验证 |
| --- | --- | --- |
| `GET /api/admin/projects/options` | T044 | 在册，`admin.scope.manage` |
| `GET /api/mailbox/sync-summary` | T043 | 在册，`mailbox.sync_self` + `RISK_ADMIN` |
| `GET /api/mailbox/review-options` | T043 | 在册 |
| `GET /api/mailbox/messages` | T043 | 在册 |
| `GET /api/mailbox/messages/{message_id}` | T043 | 在册 |
| `POST /api/mailbox/messages/{message_id}/retry` | T043 | 在册 |
| `GET /api/mailbox/sync-batches` | T043 | 在册 |
| `GET /api/mailbox/sync-batches/{batch_id}` | T043 | 在册 |

路径参数名采用 `{message_id}` / `{batch_id}`（描述式，与既有 FastAPI 端点
`{batch_id}` / `{candidate_id}` 一致），而非 NestJS reference 的 `:id`。这不构成
契约偏差：前端 `apps/web/src/api/mailbox.ts` 使用模板字符串插值
（`` `/mailbox/messages/${id}` ``）发起请求，实际 HTTP 路径
`/api/mailbox/messages/<uuid>` 与路由完全匹配；T032 candidate 的 compatibility 检查
（`packages/contracts/scripts/openapi-compat.mjs`）以**结构化路径**比对
（`path.replace(/\{[^}]*\}/g, "{}")`，将所有路径参数归约为 `{}`），因此参数名差异
不产生 breaking diff。runtime 与 OpenAPI authority 均无影响。

### 2. T043 mailbox browse/retry 契约

- **权限**：全部 7 个操作经 `require_permissions("mailbox.sync_self")`（router 依赖）+
  service 内 `_require_risk_admin`（`"RISK_ADMIN" not in identity.user.roleCodes` →
  `403 FORBIDDEN`），对齐 NestJS `ensureRiskAdmin`。
- **scope**：`_own_config` / `_own_config_for_update` 按 `MailboxConfig.userId` 定位自身
  配置；全部查询 `WHERE mailboxConfigId == config.id`，retry 使用
  `with_for_update()` 行锁。无越权扩大查询范围。
- **信封**：`ApiResponse[...]`（`code` / `message` / `data` / `traceId`）一致；
  `ok(request, ...)` 构造。
- **分页**：`MailMessageListResponse` / `MailSyncBatchListResponse` 含
  `items` / `page` / `pageSize` / `total`；query `page>=1`、`pageSize>=1,<=100`，
  对齐 NestJS `PaginatedResponse`。
- **retry 语义（ADR 0022）**：仅 `FAILED` 可重试（否则 `400`）；拒并发
  `QUEUED`/`RUNNING`（`400`）；`(uidValidity, imapUid)` source-identity 定位
  `MailSourceHandoff`（`with_for_update`）；`_retry_stage` 按失败 stage 选
  `ATTACHMENT_PARSE` / `MAIL_AI_REVIEW_PUBLISH` 并 reset 该 stage + clear 诊断；
  `enqueue_task` fresh-key（`uuid4()`，ADR 0018）；创建 `RETRY` 批次（`trigger=RETRY`，
  `retryOfId`/`targetMessageId`）；ADR 0017 metadata-only audit
  （`AuditService.record_success`，`MAIL_MESSAGE_RETRIED`）。`retryCount` 由处理 pipeline
  递增（ADR 0022 worker 职责），非 retry 端点——对齐 legacy surface。

### 3. T044 admin project-options 契约

- **路由**：`GET /api/admin/projects/options` → `ApiResponse[list[ProjectOptionResponse]]`。
- **权限**：`require_permissions("admin.scope.manage")`，对齐 NestJS
  `@RequirePermissions("admin.scope.manage")`。
- **service**：`AdminOptionsService.list_projects()` —
  `status != ARCHIVED`、`order_by name asc`、`limit 500`、LEFT JOIN `Department.name`
  （nullable）。
- **类型**：`ProjectOptionResponse`（`id: str`、`externalCode: str | None`、
  `name: str`、`departmentName: str | None`）与 `@risk-platform/contracts`
  `ProjectOption` 逐字段一致（含 nullability）。
- **additive-only**：对 T012 既有 `admin/options` 模块仅追加
  `ProjectOptionResponse` / `list_projects()` / 路由；`list_departments` /
  `DepartmentResponse` 冻结面未修改（diff 逐行确认）。composition/main 无变更
  （`options_router` / `admin_options_service` 已于 T012 注册）。

### 4. OpenAPI surface 包含这些端点（不提交 / 不恢复 T032 candidate）

live `build_openapi()` surface 已包含全部 7 个端点（见 §1）。T032 candidate 的冻结
`openapi.json` / 生成 `openapi.ts` / compatibility 检查脚本均保持 working-tree
uncommitted 状态，未提交、未重新生成、未恢复、未修改。本 Wave 不执行 T032。

### 5. 全量 validation

| 检查项 | 结果 |
| --- | --- |
| Ruff | `PASS` — `All checks passed!` |
| mypy | `PASS` — `Success: no issues found in 194 source files`（含 candidate `openapi_export.py`） |
| full pytest（PostgreSQL 16 + Redis 7） | `PASS` — `278 passed, 1 skipped`（91.82s） |
| `uv lock --check` | `PASS` — `Resolved 58 packages` |
| `git diff --check` | `PASS` — 工作树无 whitespace 错误（candidate tracked-file diff 亦 clean） |
| PostgreSQL 16 / Alembic | `PASS` — 空库 `alembic upgrade head` 至 `20260812_0008`，`alembic check` 无新 migration |

full pytest 的 1 个 skip 为既有 `tests/audit/test_audit_query_export.py:81` 数据库专项分工
提示（`TEST_DATABASE_URL 已配置；PostgreSQL 集成由数据库专项测试执行`），其 PostgreSQL 路径
已由 `tests/test_postgresql_schema.py` 与 `tests/test_schema_metadata.py` 专项覆盖。

pytest 计数从 Wave 16 的 `252 passed, 1 skipped` 增至 `278 passed, 1 skipped`，增量来自
T043 `tests/mailbox/test_sync_results.py`（`15 passed`）、T044
`tests/admin/options/test_admin_project_options.py`、T032 candidate
`tests/test_openapi_export.py`（`9 passed`，从 live app 构建，不与冻结文件 diff）及其余
既有测试在真实 PostgreSQL 16 + Redis 7 下的执行。

candidate `openapi_export.py` / `test_openapi_export.py` 经 Ruff、mypy、pytest 全绿；
candidate 对 tracked 文件的 uncommitted 修改经 `git diff --check` 无 whitespace 错误。
candidate 与本 Wave validation 共存，无需隔离或 stash。

## Integration fixes

无。T043/T044 production 代码（已 checkpoint）在真实 PostgreSQL 16 + Redis 7 环境下未暴露
integration failure；7 个端点 surface、mailbox browse/retry 契约、admin project-options
契约、permission / scope / envelope / pagination / retry 语义均按 T043/T044 acceptance
criteria 与 ADR 0017/0022 通过。未修改任何 production 代码、测试基线或 migration。
T032 candidate 文件保持 uncommitted、未触碰。

## Checkpoint

Wave 17 final checkpoint：见 `EXECUTION_STATE.md`（本次 report + 状态更新提交后记录）。

## Next-wave readiness

T032（OpenAPI authority 冻结 + 可复现前端类型生成）的 blocking 前置（T043/T044
`REVIEW_PASSED` + Wave 17 Integration `PASS`）已满足，状态同步为 `READY`（仅 metadata）。
本次不执行 T032、不启动 Wave 18；T032 candidate 文件保持 uncommitted 等待 Wave 18 重新
评估/续作。DG-05 / DG-08 保持 out of scope。
