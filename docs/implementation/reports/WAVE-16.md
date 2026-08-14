# Wave 16 Integration Report

## 结果

- Wave 16 Integration：`PASS`
- T040：`REVIEW_PASSED`
- T032：未启动
- 下一 Wave：未启动
- DG-05 / DG-08：未处理

## 范围

Wave 16 的单一工作单元为 T040：单一 owner 的 FastAPI/Celery composition root
（`composition.py` / `main.py` / `worker.py`）。Integration 验证 production composition 在真实
PostgreSQL 16 + Redis 7 + real Celery worker 环境下的端到端组装与 dispatch，不修改任何已批准
module-local 语义、migration、NestJS reference、前端或 production Compose。

本次 Integration 的首要目标是先建立可用 integration 环境，再完成验证矩阵。

## 环境 readiness

上一轮 T040 因执行环境缺少 PostgreSQL 16 / Redis 7（`TEST_DATABASE_URL` 未配置），
86 个依赖 `TEST_DATABASE_URL` 的 PostgreSQL/Redis/Celery 测试 `skipped`，real worker
integration 证据缺失。本轮显式建立可用 integration 环境：

| 服务 | 镜像 / 版本 | 状态 |
| --- | --- | --- |
| PostgreSQL | `postgres:16-alpine`（PostgreSQL 16.14） | healthy，监听 `localhost:5432`，`risk_test` 库已创建 |
| Redis | `redis:7-alpine`（Redis 7.4.10） | `PONG`，监听 `localhost:6379`，作为 Celery broker |
| Celery worker | real `solo` pool worker（`celery.contrib.testing.worker.start_worker`） | 经 Redis 7 broker 消费 `risk_platform.reliability.execute` |
| FastAPI composition | `risk_platform.main:app` production lifespan | 真实 PostgreSQL engine + 19 service 组装 |

环境变量：`TEST_DATABASE_URL` / `DATABASE_URL` 指向 `risk_test`，`CELERY_BROKER_URL=redis://localhost:6379/0`，
`DATA_ENCRYPTION_KEY` / `SESSION_SECRET_FILE` / `IMPORT_STORAGE_DIR` 均按 `.env.example` 配置。
PostgreSQL/Redis 容器通过 `infra/docker-compose.yml`（postgres）与独立 `redis:7-alpine` 容器启动，
未修改 T035 写集范围内的 `infra/docker-compose.yml`。

环境状态：`READY`（不再 `ENVIRONMENT_BLOCKED`）。

## 验证

| 检查项 | 结果 |
| --- | --- |
| 1. Production app boot / lifespan / shutdown | `PASS` — 真实 PostgreSQL 16 lifespan 内构造全部 19 个 service 并绑定 `app.state`；shutdown 释放 engine；启动期间不建表（`information_schema.tables` 计数 40 → 40 不变） |
| 2. 17 routers 与 dependency composition | `PASS` — `app.openapi()` 共 85 个路径，全部 18 个代表性 path 在册；(path, method) 唯一 |
| 3. 8 durable task handlers production merge / registration | `PASS` — `merge_worker_handlers` 覆盖全部 8 个 `DurableTaskKind`（`AGENT_EXECUTION` / `ATTACHMENT_PARSE` / `IMPORT_PREVIEW` / `MAILBOX_SYNC` / `MAIL_AI_REVIEW_PUBLISH` / `MAIL_MESSAGE_RETRY` / `RETENTION_CLEANUP` / `WEEKLY_REPORT_REBUILD`）；`register_production_worker` 对 shared `celery_app` 恰好一次调用 `register_executor`（模块级 `_registered` 守卫） |
| 4. PostgreSQL-backed services 使用正确 session/transaction | `PASS` — 全部 DB-dependent service / repository 测试在真实 PostgreSQL 16 上通过；`transaction()` caller-owned 提交/回滚语义、`FOR UPDATE NOWAIT` 并发与 audit 同事务回滚均验证 |
| 5. Redis → Celery real worker dispatch | `PASS` — `celery.contrib.testing.worker.start_worker`（`pool="solo"`）经 Redis 7 broker 消费 `risk_platform.reliability.execute`，production `merge_worker_handlers` + `register_executor` 注册的 executor 完成 fenced claim → handler 调用 → DB 状态转移；T029 real-worker success / invalid / timeout / cancellation / heartbeat / backpressure acceptance `10 passed` |
| 6. Agent handler / Provider adapter / tool registry composition | `PASS` — `build_provider` 构造受限 `AgentProviderAdapter`（immutable snapshot 解密 + `OutboundEndpointGuard` SSRF 校验）；`build_tool_registry` 组装 T028 closed read-only tool registry；`agent_execution_handlers` 被 production merge 消费而非修改 |
| 7. 无 runtime table creation / NestJS dependency / 双写 | `PASS` — lifespan 启动不建表；无 NestJS/Prisma production runtime import（`crypto.decrypt_legacy` 仅解密历史 NestJS AES-GCM triplet，非 runtime 依赖）；无双写 |
| 8. Ruff | `PASS` — `All checks passed!` |
| 9. mypy | `PASS` — `Success: no issues found in 186 source files` |
| 10. Full pytest | `PASS` — PostgreSQL 16 + Redis 7：`252 passed, 1 skipped`（上一轮 `167 passed, 86 skipped`，86 个 skipped 已转为真实执行并通过） |
| 11. `uv lock --check` | `PASS` — `Resolved 58 packages` |
| 12. `git diff --check` | `PASS` — 工作树干净 |
| 13. PostgreSQL 16 / Alembic | `PASS` — 空库 `alembic upgrade head` 至 `20260812_0008`，`alembic check` 无新 migration |

full pytest 的 1 个 skip 为既有 `tests/audit/test_audit_query_export.py:81` 数据库专项分工提示
（`TEST_DATABASE_URL 已配置；PostgreSQL 集成由数据库专项测试执行`），其 PostgreSQL 路径已由同轮
`tests/test_postgresql_schema.py` 与 `tests/test_schema_metadata.py` 专项验证覆盖。

## Integration fixes

无。production composition 在真实 PostgreSQL 16 + Redis 7 + real Celery worker 环境下未暴露
integration failure；T040 composition 与全部 module-local entry point、handler merge、shared
`celery_app` 单次 registration、lifespan 不建表、无 NestJS/双写均按 ADR 0028 addendum 与
T040 acceptance criteria 通过。未修改任何 production 代码、测试基线或 migration。

## Checkpoint

Wave 16 final checkpoint：见 `EXECUTION_STATE.md`（本次 report + 状态更新提交后记录）。

## Next-wave readiness

T032（OpenAPI authority 冻结 + 可复现前端类型生成）为下一候选工作单元，其依赖 T040 已
`REVIEW_PASSED`。本次不启动 T032，不启动下一 Wave；DG-05 / DG-08 保持 out of scope。
