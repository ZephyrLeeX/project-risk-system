# Wave 5 Integration Report

- **Wave:** 5
- **Tasks:** T009, T041
- **Result:** PASS

## 实施与 Review

- T009：完成 FastAPI auth/session、并发锁定、强制改密、撤销、Cookie 与 typed audit；初审发现的
  session secret 注入和时间精度偏离已修复，独立复审 `REVIEW_PASSED`。
- T041：完成 ADR 0018 durable task/outbox models、domain batch 引用约束和唯一 Alembic revision；
  独立审查 `REVIEW_PASSED`。
- 未修改 frontend、NestJS、Prisma、TypeScript contracts、ADR、冻结设计或 `TASK_GRAPH.md`。

## Contract / Database impact

- `/api/auth/login`、`/api/auth/session`、`/api/auth/change-password`、`/api/auth/logout` 保持 legacy
  envelope、Cookie 和 UTC 毫秒时间格式；production session key 通过 `SESSION_SECRET_FILE` 注入。
- PostgreSQL 新增 `durable_tasks`、`task_outbox`、两个 enum 和 migration `20260811_0003`；
  `import_batches`、`mail_sync_batches` 以 `NOT NULL UNIQUE`、`ON DELETE RESTRICT` 引用 task。
- Alembic 保持单一 head：`20260811_0003`。

## Integration validation

工具版本：`uv@0.12.3`、`python@3.12.13`。

- `uv sync --frozen`：PASS
- `uv lock --check`：PASS（55 packages）
- `uv run --frozen ruff check .`：PASS
- `uv run --frozen mypy .`：PASS（62 source files）
- `uv run --frozen alembic heads`：PASS（`20260811_0003 (head)`）
- PostgreSQL 16 tmpfs container，`uv run --frozen pytest -ra`：PASS（111 passed）
- `git diff --check`：PASS

首次 sandbox 内 pytest 因无法访问本机映射端口产生 25 个 connection errors；容器健康检查正常，
在获准访问本地网络的受控环境中重跑后 111 tests 全部通过。容器随后停止并自动删除，未保留 volume。

## Acceptance / Risk

- T009、T041 acceptance criteria：PASS
- `DESIGN_GAP`：无
- 未批准的 `DESIGN_DEVIATION`：无；T009 初审 findings 已修复并复审关闭
- Wave Integration：PASS
- 下一 Wave readiness：Wave 6 可调度 T008、T010；T004 仍受已记录 design gaps 阻塞。
