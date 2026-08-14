# Wave 17 Partial — T043 / T044

## 结果

- Wave 17：`IN_PROGRESS`
- 工作单元：T043（mailbox sync-results browse/retry surface）、T044（admin project-options selector）—— disjoint write-sets
- T043：`REVIEW_PASSED`（code checkpoint `eb7c9243b170feda08776912c9dcde932ca90114`，metadata `71428dadbad261d659d52aa7749978ce9c075a93`）
- T044：`REVIEW_PASSED`（code checkpoint `1dc067a44a3e5bcf5e4ecc5305e2060f713ecbd5`）

## 内容

Wave 17 由 T032 `DESIGN_GAP` resolution 拆分而来：T032 OpenAPI compatibility 检查暴露 7 个
活跃前端消费但 FastAPI authority 缺失的 `/api` 端点（1 个 admin project-options + 6 个 mailbox
sync-results），ownership 映射到新增 remediation Task T043/T044（disjoint write-sets）。

- **T043**（已完成）：新增 disjoint 子包 `mailbox/sync_results/`，迁移 NestJS
  `mail-sync-results` 的 7 个浏览/重试端点至 FastAPI，复用 `@risk-platform/contracts` 类型；
  权限 `mailbox.sync_self` + `RISK_ADMIN`，自身 `mailboxConfigId` scope，ADR 0022 retry/handoff
  语义，ADR 0017 metadata-only audit；composition/main additive 注册。详见
  `docs/implementation/reports/T043.md`。
- **T044**（已完成）：迁移 NestJS `admin.controller.ts` `@Get("projects/options")` 至
  `GET /api/admin/projects/options` → `ProjectOption[]`，权限 `admin.scope.manage`；对 T012 既有
  `admin/options` 模块 additive-only（仅追加 `ProjectOptionResponse`/`list_projects()`/路由，
  不改 `list_departments` 冻结面）；composition/main 无变更（router/service 已于 T012 注册）。
  详见 `docs/implementation/reports/T044.md`。

T043/T044 均为 T032 compatibility 缺口的 remediation owner；二者 `REVIEW_PASSED` 后 T032 的
blocking 前置（admin project-options + mailbox sync-results 端点缺失）已消除。

## 未执行

- Wave 17 Integration 未启动。
- T032（现 `BLOCKED` on T044）未执行、未 unfreeze；T032 candidate 文件保持 uncommitted、未触碰。
- T033 / T034 未执行。
- 下一 Wave 未启动。
- DG-05 / DG-08 未处理。

## 环境备注

T044 validation 使用隔离 PostgreSQL 16（`postgres:16-alpine`，容器 `project-risk-postgres` healthy）：
per-test schema `t044_<uuid>` + Alembic `upgrade head` + `seed_reference_data`；Ruff / mypy
（5 source files）/ focused pytest（`2 passed`）/ admin 回归（`12 passed`）/ composition + http_core
（`24 passed`）/ `uv lock --check` / `git diff --check` 均通过。
