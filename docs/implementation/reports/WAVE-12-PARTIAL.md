# Wave 12 Partial Report

- **Wave：** 12
- **状态：** `IN_PROGRESS`
- **已完成工作单元：** T027、T031（分别单独执行）
- **结果：** `REVIEW_PASSED`
- **代码 Checkpoint：** `8beeb062069ddc5dd6104e0b80178fcb90da9e3b`
- **T031 代码 Checkpoint：** `6545fdaf6ad4029c044c2338cc2fb36b7e385b03`

## T027 result

T027 已完成 implementation、两次独立复审和要求的 validation。weekly-report API/service/schema/task entry points、
PostgreSQL aggregate lifecycle、invalidation/reconciliation/freshness、Shanghai-week ownership，以及 ADR 0021
immutable received-time ingestion/migration contract 均已落地。shared application/worker composition 保留给 T040。

## Validation

Ruff、mypy、`uv lock --check`、`git diff --check` 全部 `PASS`；隔离 PostgreSQL 16.14 上 focused pytest
`65 passed`，包含随机 schema Alembic upgrade、legacy backfill、不可变 trigger、UIDVALIDITY reuse、aggregate
重建与 reconciliation。

## T031 result

T031 dependencies/readiness 为 `READY`：T004/T006/T008/T013/T019/T024/T025/T042 已完成，DG-04/DG-10
已有批准决议。本次随后仅执行 T031。实现包括 fixed-clock dry-run/report、受保护导入源的两阶段
`retention-deleted`/`retention-complete` 清理、Agent 会话级联清理、受限 `risk-mail-*` orphan temp 清理、
metadata-only audit，以及 ADR 0018 `RETENTION_CLEANUP` durable task/outbox entry point。

Independent Review 初次发现并推动修复 storage symlink 越界、marker crash recovery、候选饥饿、dry-run mutation
和 marker-missing residue 等问题；最终复审为 `REVIEW_PASSED`，无 `DESIGN_GAP`/`DESIGN_DEVIATION`。

最终 Ruff、mypy、`uv lock --check`、`git diff --check` 全部 `PASS`；隔离 PostgreSQL 16 focused pytest
`40 passed`，包含随机 schema Alembic `upgrade head`/`check`、锁序/保护、boundary、idempotency、partial failure、
filesystem split-brain recovery 和临时目录边界。

## Remaining / blocked

- T027：`REVIEW_PASSED`。
- T031：`REVIEW_PASSED`。
- Wave 12：保持 `IN_PROGRESS`；未启动 Integration。
- 当前 Wave 12 无 `BLOCKED` Task；DG-05/DG-08 未处理。
