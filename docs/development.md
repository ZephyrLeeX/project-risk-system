# 开发指南

## 工具链

- Node.js 22（pnpm 11.9.0 要求 Node >= 22.13）
- pnpm 11.9.0
- Python 3.12，uv
- PostgreSQL 16
- Docker Compose 用于本地数据库与生产拓扑验证

Node workspace 只有 `apps/web` 和 `packages/*`。Python 后端独立由 `apps/api/pyproject.toml` 与 `apps/api/uv.lock` 管理，不属于 pnpm workspace。

## 初始化

```sh
pnpm install --frozen-lockfile
pnpm env:init
pnpm db:up
uv sync --project apps/api --frozen
```

`pnpm env:init` 从 `.env.example` 创建本地 `.env`，随机生成本地敏感值且不覆盖已有文件。

## 启动

前端：

```sh
pnpm dev:web
```

后端另开终端：

```sh
pnpm dev:api
```

Vite 默认把 `/api` 代理到 `http://localhost:3000`。

## 验证

Node：

```sh
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
```

Python：

```sh
pnpm api:lint
pnpm api:typecheck
pnpm api:test
```

Contract：

```sh
pnpm contracts:sync
pnpm contracts:check
```

`contracts:export` 从 FastAPI OpenAPI 导出 contract，再由 contracts workspace 生成 TypeScript 类型。

## 数据库

本地 PostgreSQL：

```sh
pnpm db:up
pnpm db:status
pnpm db:logs
pnpm db:down
```

正式 schema 只由 `apps/api/alembic` 管理。不要创建第二套 migration。

## 提交前

至少执行与改动相关的 lint/typecheck/test，并对 API contract、权限、project scope、migration 和 Agent 写确认做针对性回归。不得提交 `.env`、secret、导入文件、邮件正文、数据库备份、Playwright 结果或其他运行产物。
