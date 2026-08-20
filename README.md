# 项目风险管理平台

面向项目风险、回款、周报和管理者待办的一体化内网平台。正式后端为 FastAPI，前端为 Vue 3；生产运行于单台内网服务器的 Docker Compose。

## 目录

- `apps/api`：FastAPI 模块化单体、Alembic、Celery worker/scheduler
- `apps/web`：Vue 3 / Vite 前端
- `packages/contracts`：OpenAPI 派生的 TypeScript contract
- `infra`：Compose、反向代理、备份恢复和部署脚本
- `e2e`：浏览器端到端测试
- `docs`：当前产品、架构、开发、部署、状态及 ADR
- `storage`：运行时挂载目录骨架；业务数据不提交 Git

## 本地开发

```sh
pnpm install --frozen-lockfile
pnpm env:init
pnpm db:up
uv sync --project apps/api --frozen
```

前端：`pnpm dev:web`。后端另开终端运行 `pnpm dev:api`。

常用验证：

```sh
pnpm check
pnpm api:check
pnpm contracts:check
```

## 文档

- [产品规格](docs/product-spec.md)
- [系统架构](docs/architecture.md)
- [开发指南](docs/development.md)
- [部署运维](docs/deployment.md)
- [当前状态](docs/status.md)
- [架构决策记录](docs/adr/)

不要提交 secrets、邮件正文、导入业务文件、数据库备份或其他私有运行时数据。
