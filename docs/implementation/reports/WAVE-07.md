# Wave 7 Integration Report

- **Wave：** 7
- **Result：** `FAIL`
- **范围：** 仅执行 Wave 7 Integration；未实施下一 Wave Task。

## T013 regression

唯一原 failing test 为 `tests/system_config/test_system_config.py::test_publish_contract_accepts_exact_three_levels`。根因是测试 fixture 使用单字 `高/中/低`，而当前 T013 `RiskLevelRule.displayName` contract 要求至少 2 个字符；属于过期测试期望，不是 production code 缺陷。

仅将 fixture 更新为 `高风险/中风险/低风险`。该测试单独 validation：`1 passed`。未修改 T013 production code，因此不需要新的代码 Independent Review；T013 原有 `REVIEW_PASSED` 继续有效。

## Validation

- `uv lock --check`：`PASS`
- `alembic heads`：单一 head `20260811_0004 (head)`，`PASS`
- 默认临时数据库 empty-schema `alembic upgrade head`：`PASS`
- `alembic current`：`20260811_0004 (head)`，`PASS`
- `alembic check`：`PASS`（默认临时数据库）
- T013 regression focused pytest：`PASS`（1 passed）
- Full pytest with isolated PostgreSQL 16：`FAIL`，`111 passed, 1 skipped, 32 errors`
- Full Ruff：`FAIL`，既有 T013 `system_config` findings
- Full mypy：`FAIL`，既有 T013 `system_config` findings（22 errors）
- `git diff --check`：`PASS`

## PostgreSQL blocker

PostgreSQL fixture 使用临时 PostgreSQL 16、`127.0.0.1:55439`、独立数据库 `risk_wave7`，未连接 production/shared database。数据库测试创建隔离 schema 并将 `search_path` 指向该 schema；空 schema upgrade 在 migration `20260810_0002_audit_chain.sql` 失败。

根因是 migration 在 `public` 安装 `pgcrypto`，但隔离 schema 的 `search_path` 不包含 `public`；随后 `audit_log_compute_hash` 中的 `digest(concat_ws(...), 'sha256')` 无法解析，报 `function digest(text, unknown) does not exist`。因此 T011/T012/T013/T014/T015/T021 的 PostgreSQL paths 未能完成业务断言，不能误报为 PASS。

该 blocker 不归属 T013/T012/T014/T015/T021，本次未扩大 scope 或修改 migration。临时 PostgreSQL 容器已清理，未使用 SQLite。

## Readiness

Wave 7 Integration 为 `FAIL`。下一 Wave readiness：`BLOCKED`，原因是必须先处理上述 PostgreSQL migration/search_path blocker，并重新执行完整 validation；下一 Wave 未启动。

## 2026-08-11 Integration Remediation

本次仅处理两个已知 blocker，未启动下一 Wave，也未重新实施任何已通过 Task。

### T006 migration portability

根因是 `20260810_0002` 中 pgcrypto 未显式指定 schema，且 `digest(...)` 未限定为
`public.digest(...)`；隔离 schema 且 `search_path` 不含 `public` 时函数解析失败。修复为
`CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public` 与 `public.digest(...)`，保留
metadata-only audit contract、既有 hash semantics，未恢复 snapshot/redaction。新增 isolated-schema
regression test。T006 Independent Review：`REVIEW_PASSED`。

### T013 Ruff/mypy

仅修复 T013 `system_config` 的 Ruff 与 mypy findings：格式/import/异常断言、SQLAlchemy scalar
类型收窄、ORM 局部变量类型复用和 JSON/module 字段类型收窄。未改变 T013 API/config/version
snapshot/publish semantics。T013 Independent Review：`REVIEW_PASSED`。

### Remediation validation

- Focused T006/T013：`14 passed`；T013 focused Ruff/mypy：`PASS`。
- Full Ruff：`PASS`；full mypy：`PASS`（116 source files）；`uv lock --check`：`PASS`。
- Full pytest with isolated PostgreSQL 16：`FAIL`，`143 passed, 1 failed, 1 skipped`。
- Remaining blocker：`tests/admin/roles/test_admin_roles.py::test_role_api_mutations_and_negative_audits`
  返回 422，归属 T012；root cause 是既有 update fixture 仍携带 `UpdateRoleRequest` 不允许的
  `code` 字段。本次不修改 T012。
- Default empty-schema `upgrade head` / `current` / `check`：`PASS`。
- Isolated-schema empty upgrade with `search_path` excluding `public` / `current` / `check`：`PASS`。
- Alembic single head：`20260811_0004 (head)`，`PASS`；`git diff --check`：`PASS`。

因此 Wave 7 Integration 仍为 `FAIL`，剩余 blocker 仅为 T012 regression；下一 Wave readiness
仍为 `BLOCKED`，下一 Wave 未启动。
