# Wave 24 Integration

- **Wave:** 24
- **Task:** T037 — Run full compatibility and security acceptance suite
- **结果:** `PASS`（无 integration fix；T037 write-set 为 acceptance tests only，未暴露 integration failure）
- **T037 状态:** `REVIEW_PASSED`（code checkpoint `ac05222792054bfa2a0befd709948b18430ff36e`）
- **Integration checkpoint:** 见 `EXECUTION_STATE.md`（本条目之后的提交）
- **DG-05:** 未处理（out of scope，属 T038）

## 结论

T037 `REVIEW_PASSED` + code checkpoint（`ac05222`）+ metadata checkpoint（`f6da71f`）后执行项目级联合验证，重点验证 T037 acceptance suite 与当前 production backend 的项目级联合一致性：acceptance 套件（`tests/acceptance/**`，`conftest.py` + 4 test modules，60 tests）针对真实 PostgreSQL 16 + Redis 7、真实 production FastAPI app（17 routers，经 `build_services` 组装）与隔离 Alembic `head` schema 全部通过，且与既有 frozen write-set / OpenAPI authority 联合一致。

**无 integration fix**——T037 write-set（`tests/acceptance/**` only）未暴露任何 integration failure；acceptance 套件所有断言在当前 production backend 下成立。唯一 full-suite 失败为 pre-existing flaky-timing mailbox 测试（T025 frozen write-set，超出 T037 write-set 不可编辑），已按 spec "Flaky timing/external isolation" known risk 流程重现→隔离→分类为 timing-only flaky + production fail-closed 安全，非 integration blocker，归属 T025 follow-up，不修改 T025 frozen write-set。

frozen write-sets（T035 `infra/docker-compose.yml`、T040 `composition.py`/`celery_app.py`/`worker.py`/`main.py`、T046 `scheduler.py`、T036 `infra/backup/**`）与 frozen OpenAPI authority 自各自 checkpoint 未修改；`src/risk_platform/**` production 源码自 OpenAPI freeze checkpoint `ac351cb` 未修改。

## 联合行为验证矩阵

acceptance 套件在真实 PostgreSQL 16.14（`project-risk-postgres` 容器，per-module schema `t037_<uuid>` + Alembic `head` `20260812_0008`）+ Redis 7.4.10 + 真实 production app 下逐项验证：

| # | 验证项 | 结果 |
|---|---|---|
| 1 | **production app / 17 routers / `build_services`** — acceptance harness 经 `build_services(factory, settings, cipher, import_root, ...)` + `create_app(settings, AppComposition(routers=ALL_ROUTERS, lifespan=...))` 组装全部 17 真实 router，绑定隔离 schema | PASS（`conftest.ALL_ROUTERS` 17 router 全注册；`build_app(production=True)` 选择 production env 使 cookie `Secure` + 完整 origin 校验与部署一致；真实 `build_services`，非 stub） |
| 2 | **ApiResponse / PaginatedResponse** — 13 端点 `ApiResponse` 信封 `{code,message,data,traceId}` 一致；`PaginatedResponse` `{items,page,pageSize,total}` 在 risks/audit | PASS（`test_contract_compatibility.py`；13 处 traceId/envelope/pagination 断言） |
| 3 | **401 / 403 / 404 / 422 固定错误码** | PASS（`UNAUTHORIZED`/`FORBIDDEN`/`NOT_FOUND`/`VALIDATION_ERROR` + trace header） |
| 4 | **x-trace-id** — response `x-trace-id` == body `traceId` | PASS |
| 5 | **secure session cookie** — 真实登录设置合规 cookie（`HttpOnly`/`Secure`/`SameSite=lax`/`Path=`/`expires=`）+ `mustChangePassword=false` + `/api/auth/session` 回读 `expiresAt` 一致 | PASS（`identity=None` 走真实 cookie/session 路径） |
| 6 | **DataScope=NONE** — 返回空页不改变信封 | PASS |
| 7 | **授权矩阵：四角色 × endpoints** — 24 例四角色×端点矩阵（每角色正负例，权限图来自 `seed.ROLES`） | PASS |
| 8 | **授权矩阵：五种 DataScopeType** — 精确项目集合比较（非子集） | PASS（5 `DataScopeType` 参数化，`titles == {f"风险-{name}" ...}`） |
| 9 | **授权矩阵：archived exclusion** — 归档项目跨范围排除 | PASS |
| 10 | **授权矩阵：out-of-scope 404 不泄漏为 403** — 范围外风险详情返回 404（非 leaky 403） | PASS |
| 11 | **安全：CSRF** — 未信 origin 拒绝 logout（403 + session 不失效）与 change-password | PASS |
| 12 | **安全：SSRF guard** — `OutboundEndpointGuard` 阻断 private/loopback/metadata/IPv6-loopback/IPv4-mapped、`revalidate` 检出 DNS rebinding、approved-internal allowlist | PASS（唯一 test double `_StaticResolver` 仅桩 DNS 解析，guard 真实地址分类逻辑仍执行） |
| 13 | **安全：Excel upload safety** — 非 xlsx 名 / 非 ZIP 内容 / 空 body 经真实端点 400、oversized/path-traversal/non-zip 经真实 `WorkbookStorage.validate`（`WorkbookError`） | PASS |
| 14 | **安全：Agent Provider fail-closed / no secret leakage** — `_parse_transport` 非 2xx 抛 `AgentProviderError`、每个 malformed shape 抛 `AgentProviderInvalidOutput`、仅 well-formed dict 接受、错误消息为固定安全串（`sk-secret`/`ignore_previous` 不泄漏） | PASS |
| 15 | **审计：append-only DB trigger** — 真实 `UPDATE`/`DELETE`/`TRUNCATE` 各抛 `DBAPIError`（由真实 DB trigger `audit_logs_reject_update/delete/truncate` 强制） | PASS |
| 16 | **审计：audit-chain integrity** — 真实业务写追加可验证链 link（`verify_integrity().status=="VALID"` + trace 计数 1） | PASS |
| 17 | **可靠性：idempotent enqueue** — 同 idempotency key → 同 task id + 单 outbox 行 + `dispatchGeneration=1` | PASS |
| 18 | **可靠性：crashed-worker recovery** — backdated heartbeat+lease → `reconcile` → `RETRY_WAIT` + token 清除 + `nextRetryAt` | PASS |
| 19 | **可靠性：orphan redispatch** — `create_task` gen 0 → `reconcile` → gen 1 + outbox 创建 | PASS |
| 20 | **可靠性：outbox → Celery** — `publish_outbox` → `send_task("risk_platform.reliability.execute", (taskId, 1))` + `publishedAt` 置位 | PASS（唯一 test double `_CeleryRecorder` 仅桩 `send_task`，真实 `publish_outbox` 查询/排序/`publishedAt` 变更仍执行） |
| 21 | **rollback atomicity** — 模拟下游失败 → risk（`dedupeFingerprint`）/ audit（`traceId`）/ todo（`description==suggestion`）均为 None，全部不留痕 | PASS（caller-owned `transaction()` 同事务回滚） |
| 22 | **test double 仅限已批准 3 处** — `current_identity` override / `_StaticResolver` / `_CeleryRecorder`，无核心不变式被 mock 掉 | PASS（无扩大 mocking） |

## Validation

| # | 验证项 | 结果 |
|---|---|---|
| 1 | focused acceptance pytest（隔离，真实 PostgreSQL 16 + Redis 7） | PASS（`60 passed`，11.64s） |
| 2 | full backend pytest（PostgreSQL 16 + Redis 7） | `363 passed, 1 skipped, 1 failed`（123.36s）；1 failed = pre-existing flaky-timing mailbox test，见下 |
| 3 | mailbox flaky-timing 隔离重跑 | PASS（`tests/mailbox/test_parsing.py` `10 passed` × 3 轮稳定，0.32–0.33s/轮） |
| 4 | Ruff | PASS（`All checks passed!`） |
| 5 | mypy | PASS（`Success: no issues found in 202 source files`；scope = `src`+`tests`，含 T037 acceptance tests，未变） |
| 6 | `uv lock --check` | PASS（`Resolved 65 packages`） |
| 7 | `git diff --check` | PASS（clean） |
| 8 | working tree | PASS（`git status --porcelain` 为空） |
| 9 | frozen T040 write-set（`composition.py`/`celery_app.py`/`worker.py`/`main.py`） | PASS（自 `732adfb` diff 0 行） |
| 10 | frozen T046 write-set（`scheduler.py`） | PASS（自 `21c6d3d` diff 0 行） |
| 11 | frozen T035 write-set（`infra/docker-compose.yml`） | PASS（自 `732adfb` diff 0 行） |
| 12 | frozen T036 write-set（`infra/backup/**`） | PASS（自 Wave 23 final `ad79d62` diff 0 行） |
| 13 | frozen OpenAPI authority | PASS（`packages/contracts/openapi/openapi.json` 自 `ac351cb` diff 0 行；fresh `risk-platform-openapi` 导出 byte-identical，93 paths / 243 schemas / 104 operations） |
| 14 | production 源码不变 | PASS（`src/risk_platform/**` 自 OpenAPI freeze `ac351cb` diff 0 行；T037 write-set 为 `tests/acceptance/**` only，未编辑 feature-module source/test） |
| 15 | write-set 合规 | PASS（仅 `tests/acceptance/**` 新文件；`pnpm contracts:check` / frontend validation N/A——未改 OpenAPI/前端契约） |

## Pre-existing flaky-timing 分类（非 blocker，归属 T025 follow-up）

`tests/mailbox/test_parsing.py::test_attachment_output_limit_is_metadata_only`（T025 frozen write-set，超出 T037 write-set，按协议不可编辑）。

- **重现**：full combined suite load 下返回 `PARSER_TIMEOUT`（严格断言 `OUTPUT_TRUNCATED`）。`parse_attachment("long.txt", "text/plain", b"x" * 20_000, ...)` 在隔离下 5s subprocess 预算内完成返回 `OUTPUT_TRUNCATED`；全量负载下预算被超出触发 timeout 路径。
- **隔离**：`tests/mailbox/test_parsing.py` 隔离重跑 `10 passed` × 3 轮稳定。
- **timing-only flaky 判定**：是。仅 `OUTPUT_TRUNCATED` vs `PARSER_TIMEOUT` 状态串差异，隔离稳定 PASS。
- **production fail-closed 仍安全**：是。`parsing.py:313` `PARSER_TIMEOUT` 路径返回 `result(allowed_format, "PARSER_TIMEOUT", "PARSER_TIMEOUT")`，默认 `text=""`（metadata-only，无内容泄漏）；`OUTPUT_TRUNCATED` 路径（`parsing.py:247`）同样 `text=""`。同胞测试 `test_attachment_timeout_is_metadata_only` 已显式容忍 `PARSER_TIMEOUT`（`if result.status == "PARSER_TIMEOUT": assert result.text == ""`）。安全不变式（不返回内容）始终成立。
- **结论**：known non-blocking T025 follow-up（使该测试如同胞般容忍 `PARSER_TIMEOUT`），T037 write-set 禁止 feature-module test 编辑，不修改 T025 frozen write-set。既非 inherited gap 亦非 architecture change，低严重度，不构成 Integration failure。与 Wave 23 precedent 一致（既有 flaky T029/T040 heartbeat 时序测试隔离重跑 PASS、非 blocker）。

## Integration fix

无。T037 write-set（`tests/acceptance/**` only）未暴露 integration failure；acceptance 套件全部断言在当前 production backend 下成立。未修改任何 production 代码、测试基线、migration、contract artifact、frozen write-set 或 frozen OpenAPI authority。

## Wave 25 / T038 readiness 同步（仅 metadata，未执行 T038、未启动 Wave 25）

T038（deps T037 和 DG-05）的 T037 dependency 层现已满足——T037 `REVIEW_PASSED` + Wave 24 Integration `PASS`。但 **DG-05（capacity/resilience numeric thresholds）仍未解决**（本 Wave out of scope，未处理；属 T038 explicit scope）。T038 状态保持 `BLOCKED_DESIGN_GAP (DG-05) / TODO`——T037 dependency 已满足，DG-05 仍 blocked，Wave 25 不启动。本次不执行 T038、不启动 Wave 25；DG-05 保持 out of scope。

## 停止边界（按本次授权与协议）

- **已执行** Wave 24 Integration（项目级联合验证，无 integration fix）并创建 final checkpoint。
- **未执行** T038。
- **未启动** Wave 25。
- **未处理** DG-05（performance/reliability numeric thresholds）。
- **未修改** 任何 frozen write-set（T035 `infra/docker-compose.yml`、T040 `composition.py`/`celery_app.py`/`worker.py`/`main.py`、T046 `scheduler.py`、T036 `infra/backup/**`）或 frozen OpenAPI authority；未修改 T025 frozen write-set；未修改 T033/T034 frontend cutover contract。
- 无 NestJS/Prisma runtime、SQLite 或 dual-write；未自行定义任何 DG-05 numeric threshold。
