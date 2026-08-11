# Wave 9 Integration Report

- **Wave：** Wave 9
- **状态：** `PASS`
- **日期：** 2026-08-11
- **范围：** T018/T024 integration；未启动下一 Wave
- **临时 PostgreSQL：** PostgreSQL 16，已清理

## 结果

T018 与 T024 的集成路径在测试基线修复后全部通过。此前的 6 个 failure 均确认为测试基线未同步 T024 的 `20260811_0005_mail_sync_handoff` 变更，不是运行时数据库升级失败。

## Test baseline fix

- 更新 3 个 Alembic head expectation 为 `20260811_0005`。
- 更新 downgrade message assertion 为当前 migration 的 `T024 migration is not destructively downgradable` 保护语义。
- 补齐 `mailbox_configs`、`mail_sync_batches` 和 `mail_source_handoffs` 的 T024 metadata 字段，并同步 UUID/`updatedAt` 计数。
- 仅修改测试文件：`tests/agent/test_capability_schema.py`、`tests/reliability/test_durable_task_schema.py`、`tests/test_postgresql_schema.py`、`tests/test_schema_metadata.py`。

未修改生产代码、Task definition、设计或 ADR；未发现新的 `DESIGN_GAP` 或 `DESIGN_DEVIATION`。

## Independent Review

`PASS`：测试 expectation 与当前 `20260811_0005` head、T024 migration/model metadata 及不可破坏 downgrade contract 一致；未放宽 raw mail retention、cursor、UIDVALIDITY 或 terminal-state 语义。

## Validation

| 检查项 | 结果 |
|---|---|
| Full pytest（含 PostgreSQL tests） | `PASS` — 156 passed, 1 skipped |
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

- **Wave 9 Integration：** `PASS`
- **Checkpoint：** created after this report/state update
- **Next Wave：** `NOT_READY`
- **Action：** stop; do not start the next Wave.
