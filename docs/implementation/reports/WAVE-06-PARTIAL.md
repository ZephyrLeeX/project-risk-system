# Wave 6 Partial Integration Report

- **Wave:** 6
- **Executed units:** T004, T008, T010
- **Result:** FAIL

## 实施与 Review

- T008 已完成 durable Celery reliability core，独立审查结果为 `REVIEW_PASSED`。
- T010 已完成 RBAC permission guard 与五种 project-scope predicate，独立审查结果为 `REVIEW_PASSED`。
- T004 已完成 Agent conversation/confirmation 与 weekly-report capability schemas，独立复审结果为
  `REVIEW_PASSED`。为满足 ADR 0021 的重建契约，在同一 revision 以 additive 方式登记
  `WEEKLY_REPORT_REBUILD`；未改动任务表、API、worker 或 T008/T010。Checkpoint：`1b4cfa8`。

## Integration validation

- 临时隔离 PostgreSQL 16（仅 `127.0.0.1:55436`）已用于本次 Integration；未使用 SQLite。
- API-Python Ruff：PASS。
- API-Python mypy：PASS（75 source files）。
- `uv lock --check`：PASS。
- `pytest -ra`：FAIL（93 passed，28 errors）。所有 PostgreSQL fixture 在升级至 T004 时被同一 DDL
  错误阻断，因此 T004 migration/constraint/schema、T008 durable task/outbox/lease/fencing/retry/
  reconciliation，以及 T010 project-scope permission matrix 均未能执行断言。
- Alembic heads：PASS（唯一 head `20260811_0004`）。
- empty-schema `alembic upgrade head`：FAIL；`weekly_report_items_aggregateId_sourceMailId_sourceCandidateId_riskId_key`
  超过 PostgreSQL 63 字符 identifier 上限。
- `alembic check`：FAIL（target database 未能升级到 head）。
- `git diff --check`：PASS。
- 未修改 frontend、NestJS、Prisma、TypeScript contracts、ADR、冻结设计或 `TASK_GRAPH.md`。

## Integration failure

- 阻断根因：`20260811_0004_agent_weekly_capabilities.py` 创建的
  `weekly_report_items_aggregateId_sourceMailId_sourceCandidateId_riskId_key` 长度为 74，超过 PostgreSQL
  63 字符 identifier 上限。该错误在 empty-schema upgrade 中确定复现。
- 后果：Wave 6 不能标记为 `PASS`；不创建 Wave 6 Integration checkpoint，不启动下一 Wave。
- `DESIGN_GAP` / `DESIGN_DEVIATION`：无。这是 T004 migration 实现缺陷，必须由 T004 修复并重新 Review
  后再重做 Wave 6 Integration。

## T004 Integration Fix 与重试

- T004 唯一索引名已最小修复为 `weekly_report_items_aggregate_sources_key`（41 bytes），独立 Review 为
  `REVIEW_PASSED`。T004 focused PostgreSQL tests：9 passed；empty-schema upgrade、`alembic check`、single
  head 均 PASS。
- 使用新的临时隔离 PostgreSQL 16 重新运行完整 Wave 6 pytest：FAIL（119 passed，3 failed）。
- 当前 failure 不再是 identifier：
  - T008 `test_latest_upgrade_has_one_head_and_exact_durable_constraints` 固定断言旧 head
    `20260811_0003`，实际且正确的 head 是 T004 的 `20260811_0004`。
  - `test_enum_values_and_single_alembic_head` 固定预期 AgentEventType 为 Python enum member names，而 T004
    migration 已批准并实际保存 ADR 0019 事件值 `message.delta`、`progress`、`preview`、`completed`、`error`、
    `heartbeat`。
  - `test_audit_enforcement_is_installed_by_t006` 固定只允许四个 audit trigger，未包含 T004 正确新增的两项
    Agent sequence trigger。
- 重试中的 Ruff、mypy、`uv lock --check`、Alembic single head、empty-schema upgrade、`alembic check` 和
  `git diff --check` 均 PASS。上述 3 个测试维护缺陷阻断 Wave 6 PASS；本次只授权修复 identifier，未修改它们。

## Readiness

- Wave 6：FAIL，保持未完成。
- 下一 Wave：NOT READY；须更新上述 Wave 6 integration test expectations 并完整重跑。
