# Wave 03 实施报告

## 执行结果

- Wave：Wave 3
- Task：T003 — Establish core ORM and Alembic baseline
- Task：T007 — Implement secret encryption and outbound endpoint guard
- T003 Review 结果：`REVIEW_PASSED`
- T007 Review 结果：`REVIEW_PASSED`
- Integration 结果：`PASS`
- 执行日期：2026-08-10

## 状态恢复与执行门槛

- `docs/implementation/reports/WAVE-01.md` 记录 Wave 1 Integration 为 `PASS`。
- `docs/implementation/reports/WAVE-02.md` 记录 Wave 2 Integration 为 `PASS`，并明确
  Wave 3 为 `READY`。
- 冻结的 `docs/implementation/TASK_GRAPH.md` 将 T003、T007 标记为 `READY`；二者唯一
  依赖均为已通过的 T002。
- 执行前 `main` 位于 `13bd94c`，工作树干净；仓库报告中没有 unresolved
  `DESIGN_DEVIATION`。
- DG-01 至 DG-10 均不影响 T003 或 T007；Wave 3 没有 unresolved `DESIGN_GAP`。

## 并行策略与 write set

T003 与 T007 使用独立 Implementation Agent 和隔离 Git worktree 并行执行：

- T003 独占 ORM metadata、领域 model、async DB/session/transaction、PostgreSQL fixtures
  与唯一 Alembic baseline。
- T007 独占 crypto/outbound security modules 与安全测试，并遵守 Task 要求，不修改 T002
  `Settings`、application bootstrap 或 API contract。
- T007 为 authenticated encryption 独占修改 `pyproject.toml` / `uv.lock`；T003 使用既有
  SQLAlchemy/Alembic/psycopg 依赖，不修改依赖文件。
- 两项 Task 不共享 migration、bootstrap、configuration 或 API contract ownership，因而
  不存在不可接受的 write-set collision。

集成按 T003 后 T007 的顺序执行，单一 Alembic head 保持为 `20260810_0001`，未产生
migration merge 或 lockfile 冲突。

## T003 结果

- 按最终 `schema.prisma` 和全部 13 个历史 migration 建立 28-table、29-native-enum 的
  Prisma-equivalent SQLAlchemy/Alembic baseline。
- 保持 PostgreSQL UUID、`TIMESTAMPTZ(3)`、`DECIMAL(18,2)`、JSONB、BIGINT、PK、unique、
  check、index、FK 与删除/更新语义。
- 明确排除 T006-owned `pgcrypto`、audit functions/hash enforcement 与 triggers，以及
  Seed/backfill。
- 新增 async engine/session/transaction/disposal；startup 不执行 `create_all`；破坏性
  downgrade 明确禁止。
- ORM 层最终包含 25/25 UUID Python defaults、17/17 `@updatedAt` insert/onupdate、2/2
  `DATE` annotation 与 36/36 递归 `JSONValue` annotation。

第一轮独立 Review 为 `REVIEW_FAILED`：发现 ORM 未实现 Prisma UUID/`@updatedAt` 客户端
语义，且 `DATE` / JSON Python 类型过窄。第 1 轮修复补齐全部映射和真实 PostgreSQL ORM
roundtrip tests；复审结果为 `REVIEW_PASSED`。

Reviewer 另以两个随机 schema 对照历史最终结构与 baseline，确认 28 tables、443 columns、
89 constraints、105 non-PK indexes、29 enums / 97 labels 等价。

## T007 结果

- 实现 versioned AES-256-GCM envelope、key-file ring、active/historical key rotation、masking
  与 legacy AES-GCM triplet adapter。
- 实现 Provider/IMAP shared outbound guard：HTTPS、全量 DNS answer/IP 校验、显式 hostname +
  CIDR 内网批准、不可覆盖 metadata deny、pre-connect revalidation/pinning 与 redirect
  fail-closed。
- 错误仅暴露稳定 code，不包含 secret、ciphertext、key、endpoint、hostname 或 address。
- 未实现 Provider/mailbox CRUD 或真实连接，未修改 T002 configuration/bootstrap。

第一轮 Review 为 `REVIEW_FAILED`：发现 metadata allowlist bypass 与 HTTPS downgrade。
第 1 轮修复关闭 metadata finding，但复审仍发现 encoded/double-encoded path bypass，结果为
`REVIEW_FAILED`。第 2 轮（最后一轮）采用 fail-closed relative-path policy 并补齐回归；
最终 Review 结果为 `REVIEW_PASSED`，符合最多两轮修复限制。

## Integration Validation

命令从 `project-risk-system/apps/api-python` 执行。裸 `mise exec -- ...` 会尝试在当前只读
mise data directory 安装父级 Node/pnpm，因此使用已安装版本的等价显式选择
`mise exec uv@0.12.3 python@3.12.13 -- ...`。uv cache 使用
`/tmp/wave03-integration-uv-cache`。

| 验证项 | 结果 |
| --- | --- |
| `mise exec uv@0.12.3 python@3.12.13 -- uv sync --frozen` | `PASS` — 安装锁定 `cryptography==46.0.7` 后同步成功 |
| `mise exec uv@0.12.3 python@3.12.13 -- uv lock --check` | `PASS` — `Resolved 53 packages` |
| `mise exec uv@0.12.3 python@3.12.13 -- uv run --frozen ruff check .` | `PASS` |
| `mise exec uv@0.12.3 python@3.12.13 -- uv run --frozen mypy .` | `PASS` — 47 个 source files |
| `mise exec uv@0.12.3 python@3.12.13 -- uv run --frozen alembic heads` | `PASS` — `20260810_0001 (head)` |
| `TEST_DATABASE_URL=postgresql+psycopg://lijx@127.0.0.1:55432/postgres ... pytest -ra` | `PASS` — 82 tests |
| Alembic empty-schema upgrade / check | `PASS` — pytest 为每个用例创建并清理随机 `t003_<uuid>` schema |
| PostgreSQL ORM commit / rollback / disposal | `PASS` |
| PostgreSQL schema equivalence / enum / downgrade policy | `PASS` |
| Crypto / SSRF / DNS rebinding / redirect negative tests | `PASS` |
| `git diff --check` | `PASS` |

临时 PostgreSQL 17.10 仅监听本机测试端口 55432；测试只使用随机隔离 schema，并在 teardown
执行显式清理，未修改共享 development database。首次 sandbox 内 PostgreSQL 连接因网络
限制失败；经批准以相同命令访问本机临时实例后 82 tests 全部通过。

## API / Migration / Docker

- API compatibility：`PASS` / 未受影响。Wave 3 未新增或修改 `/api` contract；既有 HTTP
  contract tests 包含在 82 个集成测试中并通过。
- Migration：`PASS`。单一 Alembic head 从空隔离 schema upgrade，`alembic check` 无 diff；
  无 runtime `create_all`，无 T006 trigger/function，未建立 Prisma 双 migration。
- PostgreSQL：`PASS`。实际 PostgreSQL 17.10 验证 UUID、时区 timestamp、numeric、JSONB、
  enum、constraint/index/FK、事务和 ORM roundtrip。
- Docker Compose：不适用。Wave 3 不拥有 production Compose，未修改现有 Compose。
- External Provider / IMAP：不适用。T007 明确不执行真实连接，未用 mock 冒充外部验收。

## Blocker 与 Design

- Blocker：无。
- 新增 `DESIGN_GAP`：无。
- 影响 Wave 3 的 unresolved `DESIGN_GAP`：无。
- `DESIGN_DEVIATION`：无。

## 风险

- 权威 schema 中 `mail_sync_batches.targetMessageId` 与 `mail_messages.batchId` 存在既有 FK
  cycle；ORM 使用 `use_alter=True` 作为 metadata 排序断点，不改变数据库约束，Alembic
  check 无 diff。
- T007 的后续 HTTP/IMAP client 必须在每次 connect 前 revalidate 并 pin 已验证 IP，同时
  保留原 hostname 做 TLS SNI/证书校验；HTTP redirect 必须关闭。
- legacy ciphertext triplet 不自带 key version；轮换前必须由部署输入明确指定 historical
  key version，不得猜测。
- `alembic upgrade head --sql` 的 offline 输出目前不是 T003 acceptance，Reviewer 将其记录为
  非阻断建议；online empty-schema upgrade/check 已通过。

## 下一 Wave

Wave 4 的 T005 与 T006 为 `READY`：二者的 T003 dependency 已为 `REVIEW_PASSED`，且没有
影响它们的已命名 unresolved `DESIGN_GAP`。

本次仅执行 Wave 3，未启动 Wave 4 或任何后续 Task。
