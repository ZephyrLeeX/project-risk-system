# Wave 8 Integration Report

- **Wave：** Wave 8
- **状态：** `PASS`
- **Final checkpoint：** `5f5d421f5bc4f7a86c0f70c1febda6c1c8ae515a`
- **范围：** T022、T023
- **日期：** 2026-08-11

## Integration 结果

| 检查项 | 结果 | 证据 |
|---|---|---|
| Full pytest（含 PostgreSQL tests） | `PASS` | `152 passed, 1 skipped`；153 项正常 collection |
| T022 collection regression | `PASS` | 原两个 collection-error 对应 tests：`2 passed, 4 skipped` |
| T022 focused pytest | `PASS` | `5 passed` |
| T022/T023 与 durable task/outbox 集成路径 | `PASS` | full pytest 覆盖通过；此前专项路径 `14 passed` |
| Ruff | `PASS` | `uv run --frozen ruff check .` |
| mypy | `PASS` | `uv run --frozen mypy .` |
| uv lock | `PASS` | `uv lock --check` |
| PostgreSQL 16 | `PASS` | 临时隔离 PostgreSQL 16 启动、连接和专项测试通过；未使用 SQLite |
| Alembic single head | `PASS` | `20260811_0004 (head)` |
| default empty-schema upgrade | `PASS` | 空数据库升级到 `20260811_0004` |
| isolated-schema upgrade | `PASS` | 隔离 schema 升级到 `20260811_0004` |
| `alembic current` | `PASS` | 默认及隔离 schema 均显示 `20260811_0004 (head)` |
| `alembic check` | `PASS` | `No new upgrade operations detected.` |
| git diff | `PASS` | `git diff --check` |

## Integration Fix 与 Independent Review

- **Root cause：** `admin.models → risk_platform.models` 动态模型聚合导入 `risks.models` 时，`risk_platform.risks.__init__` eager 导入 `risks.service`；service 反向导入尚未完成初始化的 `admin.models`，形成 package `__init__` 与 T022 service/model 边界循环。
- **Fix：** 移除 `risks/__init__.py` 的 service eager re-export；新增 `tests/risks/test_import_boundary.py` 验证 admin models 与 risk service 可独立导入。
- **Review：** Independent Review 仅检查该 import-cycle fix 与相关 tests，确认没有函数内 lazy import、隐藏副作用、检查绕过、T023 修改或 API/权限/数据库语义变化；结果 `REVIEW_PASSED`。

## PostgreSQL 与清理

使用临时 PostgreSQL 16 容器验证 full pytest、默认数据库和隔离 schema；验证完成后已移除临时容器。T022/T023 无新增 migration，继续复用既有 durable task/outbox 路径。

## Next Wave readiness

`NOT_READY`：Wave 8 已通过，但 Wave 9 的 T024 仍为 `BLOCKED_DESIGN_GAP DG-10`；T018 为 READY。Wave 9 未启动。
