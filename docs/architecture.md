# 系统架构

## 总体结构

```text
Browser
  -> HTTPS reverse proxy
     -> Vue 3 web
     -> /api -> FastAPI modular monolith
                 -> PostgreSQL
                 -> Redis -> Celery worker
                 -> scheduler
                 -> approved external integrations
```

正式运行时只有 Python FastAPI 后端。PostgreSQL 是唯一业务事实数据库；Redis 只承担 Celery broker 等批准的临时协调职责。

## 后端

`apps/api` 使用 Python 3.12、FastAPI、Pydantic、SQLAlchemy、Alembic、psycopg、Celery 和 Redis。代码按 auth/rbac/admin/imports/projects/risks/todos/timeline/system_config/ai_providers/mailbox/weekly_reports/agent/audit/reliability/retention/shared 等业务边界组织。

HTTP API 保持 `/api` 前缀。OpenAPI 是前端 contract 的最终演进方向，`packages/contracts` 保存由 OpenAPI 派生的 TypeScript 类型。

## 数据与一致性

所有正式 schema 变化通过 Alembic 管理。涉及事务、锁、约束和并发的行为以 PostgreSQL 为准。不得维护 Prisma migration 或第二套正式数据库 schema。

运行时文件通过 `project-risk-storage` Docker volume 挂载；导入文件、邮件附件和备份遵守各自留存策略，不进入 Git。

## 异步任务与调度

Celery worker 执行不适合放在请求生命周期中的工作。任务必须具有明确幂等键、retry/failure 语义和可恢复状态。scheduler 是独立 single-active 进程，负责批准的周期任务、outbox drain 和 reconciliation。

## Agent 与 AI Provider

Agent 只使用注册的业务工具。工具执行继续复用应用层权限、project scope 和审计规则。读操作不能扩大用户数据范围；持久化 mutation 必须经过显式确认。

Agent 通过 SSE 输出事件，协议必须支持生命周期、错误、取消、heartbeat/timeout 和断线恢复。Provider credential 加密保存，日志不得泄露 key、邮件正文或敏感业务载荷。Outbound 请求受允许域名/CIDR、TLS 和 SSRF 防护约束。

## 安全边界

生产只由 TLS proxy 暴露主机端口。API、PostgreSQL、Redis、worker、scheduler 与 web 位于 Compose 内部网络。Session signing key 使用只读 secret file 注入，其他生产 secrets 来自 gitignored 环境文件或受控主机文件。

权限检查、项目范围、敏感 mutation 审计、append-only audit chain、备份加密和留存保护均属于架构约束，不能为实现便利绕过。

## 架构决策

具体决策、阈值和协议以 `docs/adr/` 中已批准 ADR 为准；本文件只提供当前架构总览。
