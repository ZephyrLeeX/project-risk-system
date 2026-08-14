# Wave 18 Integration Report

## 结果

- Wave 18 Integration：`PASS`
- T032：`REVIEW_PASSED`（code checkpoint `b9a172c2aad3b68239c32dc4ac6a4c462bd85c46`）
- OpenAPI authority：FastAPI（93 paths / 243 schemas）
- compatibility check：`PASS`（7 个此前 blocking 的 breaking diff 全部消失）
- integration fix：无
- T033 / T034：未执行（仅同步 readiness）
- 下一 Wave（Wave 19 / T033、T034）：未启动
- DG-05 / DG-08：未处理

## 范围

Wave 18 为单工作单元 Wave，仅授权 T032（Freeze OpenAPI authority and generate reproducible
frontend types）。T032 已在 Wave 18 执行单元完成 `REVIEW_PASSED`（冻结 FastAPI OpenAPI 为
唯一契约权威，复现生成前端类型，7 个 breaking diff 全部消失）。本 Integration 在
T032 `REVIEW_PASSED` + code checkpoint 之后执行项目级联合验证，确认：

- 当前 production FastAPI OpenAPI 与 tracked `openapi.json` 完全一致；
- `openapi.ts` 可由 tracked OpenAPI reproducibly 生成；
- `pnpm contracts:check` 在 clean working tree 下 `PASS`；
- 前端全部 live API usage 均被 OpenAPI authority 覆盖；
- path/method/schema/enum/nullability/error envelope compatibility 全部 `PASS`；
- contracts 与 web typecheck `PASS`；
- backend Ruff / mypy `PASS`；
- full pytest（PostgreSQL 16 + Redis 7）`PASS`；
- PostgreSQL 16 / Redis 7 / Alembic 基础 integration regression `PASS`；
- `uv lock --check` / `git diff --check` `PASS`；
- 连续 export + generate 后 zero diff。

本 Wave 不修改任何 production 代码、测试基线或 migration。

## 环境

| 服务 | 镜像 / 版本 | 状态 |
| --- | --- | --- |
| PostgreSQL | `postgres:16-alpine`（PostgreSQL 16.14） | healthy，监听 `localhost:5432`，`risk_test` 库可用 |
| Redis | `redis:7-alpine`（Redis 7.4.10） | `PONG`，监听 `localhost:6379`，作为 Celery broker |

环境变量：`TEST_DATABASE_URL` / `DATABASE_URL` 指向 `risk_test`（psycopg 合法连接串，无
Prisma 风格 `?schema=` 查询参数），`CELERY_BROKER_URL=redis://localhost:6379/0`，
`DATA_ENCRYPTION_KEY` / `SESSION_SECRET_FILE` / `IMPORT_STORAGE_DIR` 按 `.env.example` 配置。
未修改 `infra/docker-compose.yml`（T035 写集范围内）；`.env` 为本环境 bootstrap 产物，被
`.gitignore` 忽略，未纳入提交。

## 验证

### 1. production FastAPI OpenAPI 与 tracked `openapi.json` 完全一致

以当前 HEAD（含 T040 composition + T043/T044 production 代码）的 production `app` 为源，
经 `risk-platform-openapi` 导出至临时文件，与 tracked
`packages/contracts/openapi/openapi.json` 逐字节比对：

- tracked：93 paths / 243 schemas
- exported：93 paths / 243 schemas
- `diff`：**IDENTICAL**（zero diff）

FastAPI 为唯一 runtime OpenAPI authority；authority 流向为 FastAPI → `openapi.json` →
`openapi.ts`；无 NestJS/Prisma runtime 依赖，无双写。

### 2. `openapi.ts` 可由 tracked OpenAPI reproducibly 生成

`pnpm contracts:gen`（`packages/contracts/scripts/gen-types.mjs`，`openapi-typescript@^7.13.0`）
读取 frozen `openapi.json` 生成 `packages/contracts/src/generated/openapi.ts`。生成产物与
tracked 文件 zero diff（`git diff --exit-code` gate 通过）。

### 3. `pnpm contracts:check` 在 clean working tree 下 `PASS`

`contracts:check` = `contracts:sync`（export + gen）后 `git diff --exit-code` 校验
`packages/contracts/openapi` 与 `packages/contracts/src/generated`。在 clean working tree
下运行，exit 0，运行后 tracked 文件无 diff。

### 4. 前端全部 live API usage 均被 OpenAPI authority 覆盖

`packages/contracts/scripts/openapi-compat.mjs`（TypeScript compiler API 静态扫描
`apps/web/src/api/*.ts`）：

- OpenAPI operations：104
- frontend API calls scanned：85
- path/method coverage：**all 85 frontend API call(s) present in OpenAPI**

7 个此前 blocking 的前端活跃消费 `/api` 端点（`GET /api/admin/projects/options` + 6 个
mailbox sync-results 浏览/重试端点 + 1 同面 detail）全部在册且 (path, method) 唯一。

### 5. path/method/schema/enum/nullability/error envelope compatibility（全部 PASS）

| 向量 | 结果 |
| --- | --- |
| path/method coverage | 85/85 frontend 调用在 OpenAPI 覆盖（104 operations） |
| error envelope | 74 个 `ApiResponse[*]` 组件 required = `{code,data,message,traceId}` |
| enum | `ROLE_CODES`（4）、`DATA_SCOPE_TYPES`（5）均为 OpenAPI superset |
| schema/nullability | `DashboardSummary.riskRemainingAmountYuan` → string（Decimal 不漂移为 number）；`riskCollectionCompletionRate` → number；`SessionResponse.expiresAt` → string（datetime 不漂移） |

compatibility check：`PASS`（no breaking differences；diff approved at T032 freeze）。

### 6. contracts 与 web typecheck

| 包 | 命令 | 结果 |
| --- | --- | --- |
| `@risk-platform/contracts` | `tsc -p tsconfig.json --noEmit` | exit 0 |
| `@risk-platform/web` | `vue-tsc -b --noEmit` | exit 0 |

### 7. backend Ruff / mypy

| 检查项 | 结果 |
| --- | --- |
| Ruff | `All checks passed!` |
| mypy（`files=["src","tests"]`） | `Success: no issues found in 194 source files` |

### 8. full pytest（PostgreSQL 16 + Redis 7）

`278 passed, 1 skipped in 91.85s`（与 Wave 17 计数一致）。

1 个 skip 为既有 `tests/audit/test_audit_query_export.py:81` 数据库专项分工提示
（`TEST_DATABASE_URL 已配置；PostgreSQL 集成由数据库专项测试执行`），其 PostgreSQL 路径
已由 `tests/test_postgresql_schema.py`（7 passed）与 `tests/test_schema_metadata.py`
（5 passed）专项覆盖。

### 9. PostgreSQL 16 / Redis 7 / Alembic 基础 integration regression

- Alembic head：`20260812_0008`（单一 head）
- 空 schema `alembic upgrade head`：成功（`20260811_0004` → … → `20260812_0008`）
- `alembic check`：`No new upgrade operations detected.`（无新 migration）
- per-test schema fixture（`tests/test_postgresql_schema.py`）在真实 PostgreSQL 16 上
  逐测试 `CREATE SCHEMA` → `upgrade head` → `check` → `DROP SCHEMA CASCADE`，7 passed
- Redis 7 经 real Celery `solo` worker 的 T029 acceptance（`tests/agent/test_agent_execution.py`）
  与 dispatcher 测试通过

### 10. `uv lock --check`

`Resolved 58 packages`，exit 0。

### 11. `git diff --check`

clean（无 whitespace 错误）；working tree 无 tracked 变更。

### 12. 连续 export + generate 后 zero diff

连续 3 轮 `pnpm contracts:sync`（export + gen），每轮后
`git status --porcelain packages/contracts/openapi packages/contracts/src/generated`
均为 zero diff；tracked `openapi.json` 与 fresh export 逐字节 IDENTICAL。export + type
generation 确定性确认。

## Integration fixes

无。T032 contract-freeze / generation 未暴露 integration failure。本 Wave 唯一需要处理的是
integration 环境 bootstrap（PostgreSQL 持久卷密码重置、`TEST_DATABASE_URL` 去除 Prisma 风格
`?schema=` 查询参数以符合 psycopg 连接串要求），均为环境配置而非 T032 契约/生成问题，未修改
任何 production 代码、测试基线、migration 或 contract artifact。7 个 breaking diff 已在 T032
执行单元（依赖 T043/T044 surface 进入 FastAPI authority）全部消除，本 Integration 复现确认。

## Checkpoint

Wave 18 final checkpoint：见 `EXECUTION_STATE.md`（本次 report + 状态更新提交后记录）。

## Next-wave readiness

仅同步下一任务 readiness，不执行下一 Task、不启动下一 Wave。

- **T033**（Cut admin pages to Python APIs and remove fixed business states；deps T016、T032）：
  T016 `REVIEW_PASSED`、T032 `REVIEW_PASSED` + Wave 18 Integration `PASS`，blocking 前置全部
  满足。T033 状态同步为 `READY`，可在 Wave 19 评估/执行。
- **T034**（Cut dashboard, weekly reports, mailbox and Agent UI to real Python APIs；deps T027、
  T030、T032、T043）：T027 / T030 / T043 均 `REVIEW_PASSED`，T032 `REVIEW_PASSED` + Wave 18
  Integration `PASS`，blocking 前置全部满足。T034 状态同步为 `READY`，可在 Wave 19 评估/执行。
- Wave 19（T033、T034 disjoint page/API modules）未启动。
- DG-05 / DG-08 保持 out of scope。
