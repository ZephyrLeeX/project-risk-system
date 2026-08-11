# Wave 9 Integration Report

- **Wave：** Wave 9
- **状态：** `FAIL`
- **日期：** 2026-08-11
- **范围：** T018/T024 integration；未启动下一 Wave
- **临时 PostgreSQL：** PostgreSQL 16，已清理

## 结果

T018 与 T024 的集成路径完成执行，但 full pytest 未通过：`150 passed, 1 skipped, 6 failed`。
失败均为测试基线未同步 T024 的 `20260811_0005_mail_sync_handoff` 变更，不是运行时数据库升级失败：

- `tests/agent/test_capability_schema.py`：仍断言最新 head 为 `20260811_0004`（owner: T004/T024 integration finding）。
- `tests/reliability/test_durable_task_schema.py`：仍断言最新 head 为 `20260811_0004`（owner: T008/T024 integration finding）。
- `tests/test_postgresql_schema.py`：head 仍断言 `20260811_0004`，并匹配旧 downgrade 文案（owner: T003/T024 integration finding）。
- `tests/test_schema_metadata.py`：仍断言 T024 新增的 `mail_source_handoffs` 不存在及旧 UUID table 数量（owner: T024 integration finding）。

未修改生产代码、Task definition、设计或 ADR；未发现新的 `DESIGN_GAP` 或 `DESIGN_DEVIATION`。

## Validation

| 检查项 | 结果 |
|---|---|
| Full pytest（含 PostgreSQL tests） | `FAIL` — 150 passed, 1 skipped, 6 failed |
| Ruff | `PASS` |
| mypy | `PASS` — no issues in 135 source files |
| `uv lock --check` | `PASS` |
| PostgreSQL 16 | `PASS` — temporary isolated instance; raw body/attachment protection and T018/T024 tests exercised |
| Alembic single head | `PASS` — `20260811_0005 (head)` |
| Default empty-schema upgrade | `PASS` |
| Isolated-schema upgrade | `PASS` |
| `alembic current` | `PASS` — `20260811_0005 (head)` |
| `alembic check` | `PASS` — No new upgrade operations detected |
| `git diff --check` | `PASS` |

## T018/T024 integration focus

T018 import commit/history paths and T024 IMAP handoff/durable task-outbox paths were collected and exercised. The mailbox tests covered UID-only envelopes; the PostgreSQL suite exercised the `mail_source_handoffs` schema, durable task/outbox migration, and isolated-schema behavior. The reviewed T024 implementation remains the source of the UID/UIDVALIDITY dedupe, terminal-state cursor, UIDVALIDITY rebaseline, retryable/permanent failure, and raw-content exclusion contracts; no corresponding runtime assertion failed in this run.

## State

- **Wave 9 Integration：** `FAIL`
- **Checkpoint：** created after this report/state update
- **Next Wave：** `NOT_READY`
- **Action：** stop; do not start the next Wave. The listed test-baseline integration finding must be resolved by its owner Task before re-integration.
