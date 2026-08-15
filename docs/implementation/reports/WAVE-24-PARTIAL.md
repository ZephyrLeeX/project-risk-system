# Wave 24 — Partial（T037 compatibility & security acceptance suite）

- **Wave:** 24
- **Tasks:** T037（单一 work unit）
- **状态:** `IN_PROGRESS`（partial — T037 `REVIEW_PASSED` + code checkpoint；Wave 24 Integration 未启动）
- **Wave 24 Integration:** 未启动
- **下一 Wave（Wave 25 / T038）/ DG-05:** 未启动 / 未处理

## 背景

T037（deps T033/T034/T036）dependency 层于 Wave 23 Integration `PASS` 后全部满足——T033/T034/T036 均 `REVIEW_PASSED` + Wave 19/20/23 Integration `PASS`。inherited design gaps 均已解决：DG-04（ADR 0027）、DG-08（ADR 0031）、DG-10（ADR 0022）；DG-05（capacity thresholds）为 T037 `Explicit out-of-scope`（属 T038）。T037 stale `BLOCKED_DESIGN_GAP (inherits DG-04/DG-05/DG-08/DG-10) / TODO` header 纠正为 `READY`（仅 metadata）→ Wave 24 标记 `IN_PROGRESS`，仅授权 T037（Lean Execution Mode）。

## Readiness

- T037 blocking 前置 T033（`REVIEW_PASSED`）、T034（`REVIEW_PASSED`）、T036（`REVIEW_PASSED`）+ Wave 19/20/23 Integration `PASS` 已满足；inherited gaps 全部解决；DG-05 为 explicit out-of-scope。无新 blocker → Wave 24 标记 `IN_PROGRESS`，仅授权 T037。
- Lean Execution Mode 加载：T037 Task、ADR 0001-0016/0018-0021、T032 frozen OpenAPI authority、T033/T034 frontend cutover contract、ADR 0031/T036 frozen write-set、必要 code/tests。

## T037 结果：`REVIEW_PASSED`

新增 `apps/api-python/tests/acceptance/**`（`conftest.py` + 4 test modules，60 tests）。

- **契约兼容性**：13 端点 `ApiResponse` 信封 + `x-trace-id` 一致；`PaginatedResponse`；固定 401/403/404/422 错误码；weekly stale 信封；真实 session cookie（HttpOnly/Secure/SameSite=lax/Path/expires）+ `mustChangePassword` + session 回读；`DataScope=NONE` 空页。
- **授权矩阵**：24 例四角色×端点矩阵（正负例）；五 `DataScopeType` 精确项目集合；归档排除；范围外 404（非 leaky 403）。
- **安全**：CSRF origin 校验（logout/change-password）；SSRF guard（private/loopback/metadata/IPv6/IPv4-mapped/DNS-rebinding/approved-internal）；Excel 上传安全（非 xlsx/非 ZIP/空/oversized/path-traversal/corrupt）；closed Agent Provider（fail-closed、malformed 拒绝、无 secret 泄漏）。
- **审计**：业务写追加可验证链 link + `verify_integrity().status=="VALID"`；append-only 真实 DB trigger 拒绝 UPDATE/DELETE/TRUNCATE。
- **可靠性**：幂等 enqueue；crashed-worker 恢复（RETRY_WAIT）；orphan gen-0 redispatch（gen 1 + outbox）；outbox→Celery 派发（`publishedAt` 置位）。
- **回滚原子性**：risk+audit+todo 全部不留痕。
- **harness**：每模块独立 PostgreSQL 16 schema（Alembic `head`）+ 真实 production app（17 routers，`build_services`）；`current_identity` override 驱动角色 + `identity=None` 走真实 cookie；NullPool 避免 cross-loop 连接复用 flake。仅 3 处明确允许的 test double。

Independent Review `REVIEW_PASSED`，零 critical/high finding（3 项 non-blocking observation，详见 `docs/implementation/reports/T037.md`）。

## Validation

- focused acceptance pytest（隔离，真实 PostgreSQL 16 + Redis 7）：`60 passed`
- full combined pytest（含 acceptance）：`363 passed, 1 skipped, 1 failed`（1 failed 为 pre-existing flaky-timing mailbox test，隔离重跑 `10 passed` 3 轮稳定，与 T037 无关，非 blocker；归属 T025 follow-up）
- mailbox flaky-timing 隔离重跑：`10 passed`（3 轮稳定）
- Ruff：`All checks passed!`
- mypy：`Success: no issues found`
- `uv lock --check`：`Resolved 65 packages`
- `git diff --check`：clean
- write-set 合规：仅 `tests/acceptance/**` 新文件；`src/risk_platform/**` 与 feature-module test 0 编辑
- `pnpm contracts:check` / frontend validation：N/A（write-set 为 acceptance tests only，未改 OpenAPI/前端契约）
- PostgreSQL 16 + Redis 7 integration：PASS

### Pre-existing flaky-timing finding（非 blocker）

`tests/mailbox/test_parsing.py::test_attachment_output_limit_is_metadata_only`（T025 frozen write-set，超出 T037 write-set，不可编辑）在 full combined suite load 下返回 `PARSER_TIMEOUT`（严格断言 `OUTPUT_TRUNCATED`，同胞测试已容忍 `PARSER_TIMEOUT`）；隔离重跑稳定 PASS；production 行为正确（timeout 返回空文本 = 安全 metadata-only）。T037 spec 列 "Flaky timing/external isolation" 为 known integration risk；既非 inherited gap 亦非 architecture change，低严重度，不构成 blocker。与 Wave 23 precedent（flaky T029/T040 heartbeat 时序测试隔离重跑 PASS、非 blocker）一致。修复归属 T025 owning-task follow-up。

## code checkpoint

T037 code checkpoint `ac05222792054bfa2a0befd709948b18430ff36e`（metadata 由后续提交补录于 `EXECUTION_STATE.md` / `T037.md`）。

## 未执行项（按本次授权与协议边界）

- Wave 24 Integration：**未启动**。
- Wave 25 / T038（capacity/resilience acceptance）：**未启动**。
- DG-05（performance/reliability numeric thresholds）：**未处理**。
- frozen write-sets（T035 `infra/docker-compose.yml`、T040 `celery_app.py`/`composition.py`/`worker.py`/`main.py`、T046 `scheduler.py`、T036 `infra/backup/**`）与 frozen OpenAPI authority：**未修改**。
- T025 mailbox flaky-timing test 修复：归属 T025 follow-up，T037 write-set 禁止 feature-module test 编辑。
