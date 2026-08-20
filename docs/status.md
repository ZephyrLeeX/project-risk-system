# 当前状态

更新时间：2026-08-20。

## 运行时基线

- 正式后端：FastAPI / Python 3.12
- 正式数据库：PostgreSQL
- 异步任务：Celery + Redis
- 调度：独立 single-active scheduler
- 前端：Vue 3 / Vite
- 部署：单服务器 Docker Compose + TLS reverse proxy
- 历史 NestJS/Prisma 已不属于生产运行时；本次仓库重构将其从当前工作树移除，历史仍可通过 Git 查阅。

## AI Agent V2

T048–T051 的实施/Review 已完成。T052 closeout 当前仍应视为 `REVIEW_FAILED`，不能因仓库清理而改写为已验收。

最新 T052 调查已经关闭“旧 Agent V1 production path 是否仍被 V2 runtime 依赖”的 blocker：旧固定 PLAN/RESPOND execution 和 legacy provider snapshot 已从 production path 移除；mail/weekly 仍使用的 provider client/service/table 保留；confirmation endpoint 按 ADR 0019 继续作为 deprecated compatibility surface，而不是 V2 runtime dependency。

尚未关闭的 T052 gate 主要是：

- 使用 patched Compose image 重新执行 Admin seeded browser E2E、fake DeepSeek full journey 和其他完整 Compose gates；
- 缺少批准的真实 DeepSeek credential 时，真实 vendor smoke 继续记录 `BLOCKED_EXTERNAL_INPUTS`，不得伪造 PASS。

因此本文件只记录当前事实，不替代独立 Review/Integration 证据。

## 仓库重构

本次重构目标是把 deployable workspace 提升到仓库根、将 FastAPI 统一为 `apps/api`、删除当前树中的 NestJS/Prisma 和过时原型/阶段报告，并把长期有效信息收口到当前文档和 ADR。

部署升级采用 bridge commit 保护历史服务器上的 gitignored `.env.production`、deploy config、secret 和 TLS cert，同时保持现有 Docker service/container/named-volume 名称不变。
