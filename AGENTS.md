# Repository Guidelines

## 1. 架构边界

正式后端是 `apps/api` 中的 Python FastAPI 模块化单体；PostgreSQL 是唯一正式数据库；Celery + Redis 承担异步任务；Agent 通过 SSE 流式交互。生产部署是单台内网服务器上的 Docker Compose。

必须保持现有前端与 `/api` contract 兼容。不得引入双后端、双写、双 migration 或运行时代理到已删除的 NestJS/Prisma 实现。

Agent 只能调用批准的受限业务工具。任何可能改变持久业务状态的 Agent 操作都必须显式确认，并保留权限、project scope 与审计边界。

## 2. 权威来源

出现冲突时按以下优先级处理：

1. 已批准的 `docs/adr/`
2. `docs/product-spec.md`
3. `docs/architecture.md`
4. 当前 production code、数据库 migration、OpenAPI contract 与测试
5. `docs/status.md` 中的当前实施状态

缺失必要设计决策时报告 `DESIGN_GAP`；实现必须偏离已批准设计时报告 `DESIGN_DEVIATION`，不得静默融合冲突来源。

## 3. 目录职责

- `apps/api`：FastAPI、领域服务、SQLAlchemy/Alembic、Celery、测试
- `apps/web`：Vue 3 / Vite 正式前端
- `packages/contracts`：OpenAPI 生成的 TypeScript contract
- `infra`：正式部署、代理、备份恢复
- `e2e`：浏览器 E2E
- `storage`：仅保留目录骨架；运行时内容不得提交

## 4. 后端实现规范

按业务领域聚合 API/schema/service/repository/domain/policy/tasks/tests。避免巨型全局 router/service/model、隐藏循环依赖和大面积 `Any`。涉及 schema、constraint、index、transaction、concurrency 或 locking 时必须以 PostgreSQL 行为为准。

重型后台工作不得塞进 FastAPI request lifecycle。Celery task 要明确输入、幂等、retry、失败处理和序列化边界；Redis 不得变成业务事实源。

SSE 必须处理 event contract、连接生命周期、错误、取消、heartbeat/timeout 与 partial response。

## 5. API、安全与数据

修改 API 时检查 method/path/query/body、response fields、status/error shape、pagination、时间序列化、nullability、enum 和 permission behavior。不能只验证返回 200。

不得绕过 permission checks、project-scope checks、sensitive mutation audit。真实 secrets 只能通过环境变量或批准的 secret file 注入。

不要向 Git 提交 credentials、邮件正文、用户敏感文件、业务导入文件、数据库备份或其他私有运行时数据。

## 6. 工具链与验证

Node 使用当前 `package.json` / `pnpm-lock.yaml`；Python 使用 `apps/api/pyproject.toml` / `apps/api/uv.lock`。常用验证：

```sh
pnpm install --frozen-lockfile
pnpm check
pnpm contracts:check
cd apps/api
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen mypy .
uv run --frozen pytest -ra
```

不得通过删除测试、降低断言、大量 skip、禁用类型检查或 lint 规则来制造通过结果。版本与最终命令应以当前仓库配置为准。
