# Wave 7 Partial Report

- **Wave:** 7
- **Executed units:** T011, T012, T013, T014, T015, T017, T021
- **Result:** IN_PROGRESS；Wave 7 Integration：`FAIL`

## T011

- T011 user administration implementation and independent Review：`REVIEW_PASSED`。
- API contract、PostgreSQL transaction/audit regression、Ruff、mypy、lockfile 与完整 PostgreSQL test
  suite 均通过（124 passed）。
- T011 checkpoint：`baa3208`。

## T012

- T012 role/permission/department administration implementation and independent Review：`REVIEW_PASSED`。
- API contract、保护系统角色、权限边界、失败审计、Ruff、mypy 与全量 Python tests 已验证。
- Full validation：`96 passed, 32 skipped`；T012 PostgreSQL 集成测试因 `TEST_DATABASE_URL` 未配置而 skipped。
- T012 checkpoint：`7361d26`。
- T013 已完成实现与独立 Review：`REVIEW_PASSED`；新增系统配置版本、发布、历史、快照和项目选项 API。PostgreSQL validation 因 `TEST_DATABASE_URL` 未配置而 skipped；checkpoint：`13463f3`。
- T014 已完成实现与独立 Review：`REVIEW_PASSED`；新增 AI Provider 管理、真实连接测试、加密密钥轮换、选择策略、使用统计和 metadata-only call log。定向 validation 全部通过；完整 pytest 的唯一失败属于既有 T013 测试不一致；PostgreSQL 因 `TEST_DATABASE_URL` 未配置而 skipped；checkpoint：`d9bde24`。

## T015

- T015 已完成审计查询、详情、summary/options、hash-chain integrity 和 metadata-only CSV/XLSX export；独立 Review：`REVIEW_PASSED`。
- T015 定向 validation：Ruff、mypy、5 个测试均通过；PostgreSQL 因 `TEST_DATABASE_URL` 未配置而 skipped，未使用 SQLite。
- Full pytest：`104 passed, 33 skipped, 1 failed`；唯一 failure 属于既有 T013 `system_config` 测试，保留为 Wave 7 Integration 待处理项。Full Ruff/mypy 的既有 T013 findings 同样未在 T015 中处理。
- T015 checkpoint：`dd603dc`。
- T017 已完成实现与 Independent Review：`REVIEW_PASSED`。Ruff、mypy、`uv lock --check`、focused pytest（`3 passed`）通过；full pytest 为 `107 passed, 33 skipped, 1 failed`，唯一 failure 是已知 T013 regression，保留为 Wave 7 Integration 待处理项。T017 无独立 PostgreSQL-specific test，记为 `N/A`；其他 PostgreSQL tests 因未配置 `TEST_DATABASE_URL` skipped，未使用 SQLite。
- T021 已完成实现与 Independent Review：`REVIEW_PASSED`。新增 scoped todo list/detail/update、summary/schedule、事务性 timeline/audit hooks 和 one-auto-todo-per-risk service。T021 Ruff、mypy、lock check 与 focused pytest（`3 passed`）通过；full pytest 为 `110 passed, 33 skipped, 1 failed`，唯一 failure 是已知 T013 regression，继续保留为 Wave 7 Integration 待处理项。T021 PostgreSQL API validation 因 `TEST_DATABASE_URL` 未配置而 skipped，未使用 SQLite。
- T017 checkpoint commit：`7c4b2e60f1b54ad11c18caeae6a65a440026e9db`；详见 `reports/T017.md`。
- T021 已完成。Wave 7 Integration 已执行但 `FAIL`；详见 `reports/WAVE-07.md`。下一 Wave 未启动。

## Readiness

- T017：`REVIEW_PASSED`；T021：`REVIEW_PASSED`。
- Wave 7 Integration：`FAIL`；下一 Wave readiness：`BLOCKED`。阻塞项为 T006 audit migration 在隔离 schema `search_path` 下无法解析 pgcrypto `digest`，以及既有 T013 full Ruff/mypy findings；详见 `reports/WAVE-07.md`。
