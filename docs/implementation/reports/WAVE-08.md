# Wave 8 Integration Report

- **Wave：** Wave 8
- **状态：** `FAIL`
- **范围：** T022、T023
- **日期：** 2026-08-11

## Integration 结果

| 检查项 | 结果 | 证据 |
|---|---|---|
| Full pytest（含 PostgreSQL tests） | `FAIL` | 收集到 146 项；`tests/admin/roles/test_admin_roles.py` 与 `tests/admin/users/test_admin_users.py` 在 collection 阶段因同一循环导入失败；未进入完整执行 |
| T022/T023 与 durable task/outbox 集成路径 | `PASS` | `pytest tests/risks tests/timeline tests/mailbox tests/reliability`：14 passed |
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

## Integration finding

- **失败测试：** `tests/admin/roles/test_admin_roles.py`、`tests/admin/users/test_admin_users.py` collection errors。
- **Root cause：** 导入 `risk_platform.admin.models` 时，`risk_platform.models` 的动态模型聚合导入 `risk_platform.risks.models`；风险包初始化又导入 `risks.service`，该 service 反向导入尚未完成初始化的 `admin.models`，形成循环导入。
- **Owner Task：** T022（风险模块导入路径）。
- **处理边界：** 本次仅记录 integration finding；未修改生产代码，未实施其他 Task，未启动下一 Wave。

## PostgreSQL 与清理

使用临时 PostgreSQL 16 容器验证默认数据库和隔离 schema；验证完成后已移除临时容器。T023 无新增 migration；T022/T023 复用既有 durable task/outbox 路径。

## Next Wave readiness

`NOT_READY`：Wave 8 Integration 为 `FAIL`，需由 owner Task 处理循环导入并重新执行本 Wave Integration；下一 Wave 未启动。
