# Wave 19 Integration Report

## 结果

- Wave 19 Integration：`PASS`
- T045：`REVIEW_PASSED`（code checkpoint `ac351cb8aed4ccfd35a13fdf4becbdc2e80f22df`；metadata checkpoint `13d21c3ad1e491be9dbd578ad4353d330364e86e`）
- OpenAPI authority：FastAPI（93 paths / 243 schemas / 104 operations）
- production FastAPI OpenAPI 与 tracked `openapi.json`：byte-identical
- compatibility check：`PASS`（no breaking diff）
- integration fix：无
- T033 / T034：未执行（仅同步 readiness 为 `READY`）
- 下一 Wave（Wave 20 = T033、T034）：未启动
- DG-05 / DG-08：未处理

## 范围

Wave 19 为单工作单元 Wave，仅授权 T045（Restore `_Contract` serialization-mode OpenAPI
schema fidelity）。T045 已在 Wave 19 执行单元完成 `REVIEW_PASSED`：删除三个模块
（`admin/overview`、`weekly_reports`、`agent`）共享的 `_Contract` 基类通配 field
serializer 的 `-> object` 返回注解（加 `# type: ignore[no-untyped-def]`），序列化函数体逐字节
不变；re-freeze `openapi.json`/`openapi.ts` 后受影响 schema 恢复真实字段类型。本 Integration 在
T045 `REVIEW_PASSED` + code/metadata checkpoint 之后执行项目级联合验证，确认：

- T045 修复后的 production FastAPI OpenAPI 与 tracked `openapi.json` 完全一致；
- API surface 仍为 93 paths / 243 schemas / 104 operations；
- admin overview / weekly reports / agent 的 serialization-mode schema 不再退化为 `unknown` / 裸 `{}`；
- T033/T034 所需字段在 generated `openapi.ts` 中保持真实类型；
- runtime JSON 序列化与 T045 前保持兼容；
- `pnpm contracts:check` clean-tree `PASS`；
- OpenAPI compatibility check 无 breaking diff；
- contracts + web typecheck `PASS`；
- backend Ruff / mypy `PASS`；
- full pytest（PostgreSQL 16 + Redis 7）`PASS`；
- PostgreSQL 16 / Alembic regression `PASS`；
- 连续 export + generate deterministic zero diff；
- `uv lock --check` / `git diff --check` `PASS`。

本 Wave 不修改任何 production 代码、测试基线、migration 或 contract artifact。

## 环境

| 服务 | 镜像 / 版本 | 状态 |
| --- | --- | --- |
| PostgreSQL | `postgres:16-alpine`（PostgreSQL 16） | healthy，监听 `localhost:5432`，`risk_test` 库可用 |
| Redis | `redis:7-alpine`（Redis 7.4.10） | `PONG`，监听 `localhost:6379`，作为 Celery broker |

环境变量：`TEST_DATABASE_URL` / `DATABASE_URL` 指向 `risk_test`（psycopg 合法连接串，无
Prisma 风格 `?schema=` 查询参数），`CELERY_BROKER_URL=redis://localhost:6379/0`，
`DATA_ENCRYPTION_KEY` / `SESSION_SECRET_FILE` / `IMPORT_STORAGE_DIR` 按 `.env.example` 配置。
未修改 `infra/docker-compose.yml`；`.env` 为本环境 bootstrap 产物，被 `.gitignore` 忽略，未纳入提交。

## 验证

### 1. production FastAPI OpenAPI 与 tracked `openapi.json` 完全一致

以当前 HEAD（含 T045 修复）的 production `app`（T040 composition）为源，经
`risk-platform-openapi` 导出至临时文件，与 tracked
`packages/contracts/openapi/openapi.json` 逐字节比对：

- tracked：93 paths / 243 schemas / 104 operations
- exported：93 paths / 243 schemas / 104 operations
- `cmp`：**BYTE IDENTICAL**（zero diff）

FastAPI 为唯一 runtime OpenAPI authority；authority 流向为 FastAPI → `openapi.json` →
`openapi.ts`；无 NestJS/Prisma runtime 依赖，无双写。

### 2. API surface 仍为 93 paths / 243 schemas / 104 operations

```
paths: 93
schemas: 243
operations: 104
```

与 Wave 18 冻结基线一致；T045 fidelity 修复未改变 path/method/operation 数量，仅修正字段级
schema 形状。

### 3. admin overview / weekly reports / agent serialization-mode schema 不再退化

逐项确认 frozen OpenAPI authority 中受影响 `_Contract` schema 的字段恢复真实类型
（`format: date-time` / `enum` / `$ref` / `type` / `anyOf`），无退化 `{"title": ...}`（无
`type`/`enum`/`anyOf`/`$ref`）或裸 `{}`：

| schema | 代表字段 → 真实类型 |
| --- | --- |
| `HealthItem` | `checkedAt`=`{date-time,string}`；`key`/`status`=`enum`；`link`=`$ref OverviewLink \| null` |
| `AttentionItem` | `occurredAt`=`{date-time,string}`；`kind`/`status`=`enum`；`link`=`$ref OverviewLink` |
| `AdminOverview` | `generatedAt`=`{date-time,string}`；`health`/`attention`/`recentAudit`=`$ref array \| null` |
| `WeeklyReportResponse` | `freshnessDeadline`/`generatedAt`=`{date-time,string}`；`weekStart`/`weekEnd`=`{date,string}`；`projects`=`$ref array` |
| `WeeklyProjectDetail` | `generatedAt`=`{date-time,string}`；`project`=`$ref`；`items`=`$ref array` |
| `AgentConfirmationResponse` | `completedAt`=`{date-time,string}`；`resourceId`=`{uuid,string}` |
| `AgentConversationResponse` | `createdAt`/`expiresAt`/`updatedAt`=`{date-time,string}`；`id`=`{uuid,string}` |
| `AgentMessageResponse` | `createdAt`=`{date-time,string}`；`dataAsOf`=`{date-time,string \| null}`；`id`=`{uuid,string}` |

### 4. T033/T034 所需字段在 generated `openapi.ts` 中保持真实类型

`openapi.ts` 中受影响字段由 `unknown` 恢复为真实类型，例如：

```typescript
AttentionItem: {
    id: string;
    kind: "IMPORT_REVIEW" | "AI_PROVIDER_CONNECTION" | "AI_PROVIDER_EXPIRY";
    link: components["schemas"]["OverviewLink"];
    occurredAt: string;   // Format: date-time
    status: "CRITICAL" | "WARNING";
    summary: string;
    title: string;
};
```

`unknown` 残留计数 191，均为既有合法 `JSONValue`/opaque-dict 字段（如
`WeeklyReportResponse.summary: dict[str, JSONValue]`），非退化。T033（admin overview）与
T034（weekly reports / agent UI）前端类型可见性 blocker 已消除。

### 5. runtime JSON 序列化与 T045 前保持兼容

T045 仅删除 serializer 返回注解，序列化函数体逐字节不变；`when_used="json"` /
`check_fields=False` / `extra="forbid"` 保留。`tests/test_openapi_export.py` 中
`test_contract_datetime_runtime_json_is_utc_milliseconds_with_z` 断言 runtime JSON 为
`...T..:..:...123Z`（UTC 毫秒 + `Z`）且 Python `model_dump()` 仍返回 `datetime` 对象；该测试
随 full pytest（283 passed）通过。runtime JSON 行为与 T045 前兼容。

### 6. `pnpm contracts:check` clean-tree PASS

`contracts:check` = `contracts:sync`（export + gen）后 `git diff --exit-code` 校验
`packages/contracts/openapi` 与 `packages/contracts/src/generated`。在 clean working tree
下运行，exit 0，运行后 tracked 文件 zero diff（`git status --porcelain` 为空）。

### 7. OpenAPI compatibility check 无 breaking diff

`packages/contracts/scripts/openapi-compat.mjs`：

- OpenAPI operations：104
- frontend API calls scanned：85
- path/method coverage：all 85 frontend API call(s) present in OpenAPI
- error envelope：74 个 `ApiResponse[*]` 组件 required = `{code,data,message,traceId}`
- enum `ROLE_CODES`（4）/ `DATA_SCOPE_TYPES`（5）均为 OpenAPI superset
- schema/nullability spot-check 全部通过

compatibility check：`PASS`（no breaking differences；T045 fidelity 修复未引入新差异）。

### 8. contracts + web typecheck

| 包 | 命令 | 结果 |
| --- | --- | --- |
| `@risk-platform/contracts` | `tsc -p tsconfig.json --noEmit` | exit 0 |
| `@risk-platform/web` | `vue-tsc -b --noEmit` | exit 0 |

### 9. backend Ruff / mypy

| 检查项 | 结果 |
| --- | --- |
| Ruff | `All checks passed!` |
| mypy（`files=["src","tests"]`） | `Success: no issues found in 185 source files` |

### 10. full pytest（PostgreSQL 16 + Redis 7）

`283 passed, 1 skipped in 92.05s`（较 Wave 18 `278 passed` 增 5 个 T045 schema-fidelity 测试）。

1 个 skip 为既有 `tests/audit/test_audit_query_export.py:81` 数据库专项分工提示
（`TEST_DATABASE_URL 已配置；PostgreSQL 集成由数据库专项测试执行`），其 PostgreSQL 路径
已由 `tests/test_postgresql_schema.py`（7 passed）与 `tests/test_schema_metadata.py`
（5 passed）专项覆盖。`tests/test_openapi_export.py` `14 passed`。

### 11. PostgreSQL 16 / Alembic regression

- Alembic head：`20260812_0008`（单一 head）
- 空库 `alembic upgrade head`：成功（`20260811_0004` → … → `20260812_0008`）
- `alembic check`（fresh DB）：`No new upgrade operations detected.`
- per-test schema fixture（`tests/test_postgresql_schema.py`）在真实 PostgreSQL 16 上
  逐测试 `CREATE SCHEMA` → `upgrade head` → `check` → `DROP SCHEMA CASCADE`，7 passed
- Redis 7 经 full pytest（含 T029/T026 real Celery `solo` worker 路径）通过

### 12. 连续 export + generate deterministic zero diff

连续 3 轮 `pnpm contracts:sync`（export + gen），每轮后
`git status --porcelain packages/contracts/openapi packages/contracts/src/generated`
均为 zero diff；`openapi.json` 与 `openapi.ts` SHA-256 三轮不变：

```
round 1: json=dbe1e91b…  ts=9f66788d…  dirty=0
round 2: json=dbe1e91b…  ts=9f66788d…  dirty=0
round 3: json=dbe1e91b…  ts=9f66788d…  dirty=0
```

export + type generation 确定性确认。

### 13. `uv lock --check`

`Resolved 58 packages`，exit 0。

### 14. `git diff --check`

clean（无 whitespace 错误）；working tree 无 tracked 变更。

## Integration fixes

无。T045 contract-fidelity remediation 未暴露 integration failure。本 Wave 唯一的环境工作
是 PostgreSQL 16 + Redis 7 bootstrap（已由 Wave 16+ 建立），均为环境配置而非 T045 契约/生成
问题。未修改任何 production 代码、测试基线、migration 或 contract artifact。

## Checkpoint

Wave 19 final checkpoint：见 `EXECUTION_STATE.md`（本次 report + 状态更新提交后记录）。

## Next-wave readiness

仅同步下一任务 readiness，不执行下一 Task、不启动下一 Wave。

- **T033**（Cut admin pages to Python APIs and remove fixed business states；deps T016、T032）：
  T016 `REVIEW_PASSED`、T032 `REVIEW_PASSED`、T045 `REVIEW_PASSED` + Wave 19 Integration `PASS`，
  且 T045 已恢复 admin overview item 级字段的 OpenAPI/`openapi.ts` 真实类型可见性。blocking 前置
  全部满足。T033 状态同步为 `READY`，可在 Wave 20 执行。
- **T034**（Cut dashboard, weekly reports, mailbox and Agent UI to real Python APIs；deps T027、
  T030、T032、T043）：T027 / T030 / T043 均 `REVIEW_PASSED`，T032 `REVIEW_PASSED`，T045
  `REVIEW_PASSED` + Wave 19 Integration `PASS`，且 T045 已恢复 weekly reports / agent item 级
  字段的 OpenAPI/`openapi.ts` 真实类型可见性。blocking 前置全部满足。T034 状态同步为 `READY`，
  可在 Wave 20 执行。
- Wave 20（T033、T034 disjoint page/API modules）未启动。
- DG-05 / DG-08 保持 out of scope。
