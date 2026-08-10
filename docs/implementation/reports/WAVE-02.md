# Wave 02 实施报告

## 执行结果

- Wave：Wave 2
- Task：T002 — Build HTTP, configuration and tracing core
- 实施结果：`IMPLEMENTED`
- Review 结果：`REVIEW_PASSED`
- Integration 结果：`PASS`
- 执行日期：2026-08-10

## 状态恢复与执行门槛

- Git 执行前工作树干净，`main` 最新提交为 `10addd7`，提交说明为
  `backend: complete wave 1 workspace bootstrap`。
- `docs/implementation/reports/T001.md` 记录 T001 实施和验证为 `PASS`。
- `docs/implementation/reports/WAVE-01.md` 记录 T001 独立 Review 为
  `REVIEW_PASSED`、Wave 1 Integration 为 `PASS`，并明确 Wave 2 为 `READY`。
- 冻结的 `docs/implementation/TASK_GRAPH.md` 明确 Wave 2 仅包含 T002；T002 唯一依赖
  T001，Task 状态为 `READY`，没有影响 T002 的已命名未解决 `DESIGN_GAP`。
- 恢复时没有 unresolved `DESIGN_DEVIATION`。Task Graph 中保留的 DG-01 至 DG-10
  影响后续指定 Task，不影响 T002 或 Wave 2 的执行门槛。

## 实施摘要

- 新增 FastAPI app factory、ASGI 入口、validated `Settings` 和 `/api` router bootstrap。
- 实现 `GET /api/health`，保持 legacy `HealthResponse` 字段及
  `{code,message,data,traceId}` envelope。
- 实现 request trace、统一异常映射、安全 catch-all、CORS、显式 proxy trust 和
  session Cookie 安全默认 contract。
- 新增 `StrictRequestModel`，保持 unknown request fields 的拒绝语义。
- 新增 `AppComposition`，允许 feature router、dependency overrides 和 lifespan 在模块
  测试及 T040 中组合，无需编辑共享 bootstrap。
- `.env.example` 仅同步 T002 实际读取的 `TRUSTED_PROXY_CIDRS`。该最小范围扩展用于满足
  repository 对新增配置键同步模板的强制要求。
- 未实现 DB readiness、auth 业务、dynamic dependency health 或任何后续 Wave 功能。

## 独立 Review

第一轮 Review 结果为 `REVIEW_FAILED`，发现四项问题：共享 unknown-input contract 缺失、
未处理异常可能向 ASGI server 泄露、中间件顺序导致 500 缺少 CORS/preflight 缺少 trace，
以及 401/403 回归测试不足。

Implementation Agent 在第一轮修复中增加共享 `StrictRequestModel`、
`SafeExceptionMiddleware`，调整 middleware 层次，并补齐 401/403/404/405、500+CORS、
OPTIONS trace、日志脱敏及异常传播测试。

第二轮 Reviewer 重新读取最新 diff、测试与报告并执行独立探针，确认四项 findings 均关闭。
最终 Review 结果：`REVIEW_PASSED`。

## Integration Validation

命令均从 `project-risk-system/apps/api-python` 运行。非交互 shell 未暴露 uv 到 `PATH`，
因此使用 mise 安装的 uv 绝对路径；`UV_CACHE_DIR` 使用 `/tmp/wave02-uv-cache`。

| 验证项 | 结果 |
| --- | --- |
| `uv sync --frozen` | `PASS` — `Checked 48 packages` |
| `uv lock --check` | `PASS` — `Resolved 50 packages` |
| `uv run --frozen ruff check .` | `PASS` — `All checks passed!` |
| `uv run --frozen mypy .` | `PASS` — 24 个 source files 无问题 |
| `uv run --frozen pytest -ra` | `PASS` — 21 个 tests 通过 |
| generated OpenAPI smoke | `PASS` — OpenAPI 3.1.0 包含 `/api/health` |
| `git diff --check` | `PASS` |

## API / Migration / Docker

- API compatibility：`PASS`。`GET /api/health` 的 path、health data、成功 envelope、错误
  envelope、status 及 trace 由 HTTP contract tests 和 OpenAPI smoke 验证；未修改前端、
  NestJS 或 TypeScript contracts。
- PostgreSQL validation：不适用。T002 不包含数据库连接、schema 或 PostgreSQL 行为。
- Migration validation：不适用。未新增 SQLAlchemy model、DDL 或 Alembic revision。
- Docker Compose validation：不适用。T002 不负责 image、Compose、proxy deployment 文件。
- Redis / Celery / external provider / mailbox validation：不适用。

## Blocker 与 Design

- Blocker：无。
- 新增 `DESIGN_GAP`：无。
- 影响 Wave 2 的 unresolved `DESIGN_GAP`：无。
- `DESIGN_DEVIATION`：无。

## 风险

- 当前测试 transport 使用与锁定 Starlette 1.6.0 对应的 `httpx2.ASGITransport`；后续测试
  应复用当前方式，或在升级依赖时重新验证同步 client 行为。
- 后续 feature request DTO 必须继承 `StrictRequestModel`，否则无法保持 unknown-field
  compatibility。
- production 必须为 `TRUSTED_PROXY_CIDRS` 配置实际反向代理网段，不得使用宽泛公网 CIDR。
- T009 应复用现有 Cookie security contract，不得降低 production `Secure` 默认值。
- uv 未加入当前非交互 shell 的 `PATH`；这属于执行环境风险，不影响锁文件和应用行为。

## 下一 Wave

Wave 3 为 `READY`：冻结 Task Graph 中 Wave 3 包含 T003 与 T007；二者所需的 T002 依赖
现已 `REVIEW_PASSED` 且 Wave 2 Integration 为 `PASS`，Task Graph 将二者标记为
`READY`，没有影响它们的已命名 `DESIGN_GAP`。

本次未启动 Wave 3。
