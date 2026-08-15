# Wave 23 Integration

- **Wave:** 23
- **Task:** T036 — Implement encrypted backup and restore runbook
- **结果:** `PASS`（1 项 integration fix，位于 T036 write-set `infra/backup/**` 内）
- **T036 状态:** `REVIEW_PASSED`（code checkpoint `5824695f73c1c31e272b17e88324e13b674b80d1`）
- **Integration checkpoint:** 见 `EXECUTION_STATE.md`（本条目之后的提交）
- **DG-05:** 未处理（out of scope）

## 结论

T036 `REVIEW_PASSED` + code checkpoint（`5824695`）+ metadata checkpoint（`27dabf2`）后执行项目级联合验证，重点验证 T036 backup/restore 与既有 persistence/security/retention/deployment 体系的项目级联合一致性。ADR 0031 加密备份/恢复契约（quiesce 协调一致备份集合、AES-256-GCM 信封加密、manifest 绑定三层完整性、版本化 KEK + 保留历史可恢复性、fail-closed 隔离恢复、metadata-only 日志、one-shot 命令不重开 T035 Compose）在真实 PostgreSQL 16 + 真实 `pg_dump -Fc`/`pg_restore`（经 `docker exec` 运行 `postgres:16-alpine` 二进制）下联合一致。

**1 项 integration fix**：CLI restore 路径 `cmd_restore` 经 `_build_pg_dumper(args)` 读取 `args.dsn`，但 restore 子解析器只定义 `--target-dsn`（无 `--dsn`），导致 `AttributeError: 'Namespace' object has no attribute 'dsn'`。根因：backup/restore 单元测试与真实 PostgreSQL drill 均直接调用 `run_backup`/`run_restore`，从未驱动 CLI 子命令，故该 CLI 路径未被覆盖。最小修复（write-set 内）：`_build_pg_dumper(args, dsn)` 显式接收 DSN，`cmd_backup` 传 `args.dsn`、`cmd_restore` 传 `args.target_dsn`（`cli.py` +4/−4）。新增 `tests/test_cli.py`（2 个 CLI wiring 回归测试）防回归。修复后 CLI 端到端 smoke 全绿。

5 项 Independent Review non-blocking observations 经 integration 评估均确认为已批准/非阻断行为，不升级为 integration blocker，不扩 scope 修改（详见下文）。frozen write-set（`scheduler.py`/`composition.py`/`main.py`/`worker.py`/`celery_app.py`）、`infra/docker-compose.yml`、frozen OpenAPI authority 自 `732adfb`/`ac351cb` 未修改；未引入 WAL/PITR、cloud backup 或 DG-05 数值决策。

## 联合行为验证矩阵

以真实 PostgreSQL 16.14（`project-risk-postgres` 容器）+ Redis 7.4.10 + `apps/api-python` uv 环境（提供 `cryptography`/`psycopg`/`risk_platform.shared.crypto`）验证：

| # | 验证项 | 结果 |
|---|---|---|
| 1 | **Backup authority** — PostgreSQL + durable `project-risk-storage` 为备份权威；Redis/Celery/cache/temp/log 不进入 backup | PASS（`backup.py` 仅 `pg_dump` + `archive_files(storage_root)`；`DEFAULT_EXCLUDES` 排除 `__pycache__`/`*.tmp`/`*.log`；manifest `pg`/`files` 两组件，无 Redis/queue/temp 组件） |
| 2 | **Coordinated quiesce** — scheduler/worker/api 停止 → capture → cleanup → unquiesce；任一步失败不得产生可用 backup | PASS（`ComposeQuiescer` 停 api/worker/scheduler；quiesce 失败 → `INCOMPLETE` 无 artifact；unquiesce 失败 → artifact 仍 USABLE + surfaced warning；`test_quiesce_failure_is_fail_closed_no_artifact`/`test_unquiesce_failure_keeps_usable_artifact_with_warning`） |
| 3 | **Real PostgreSQL 16 backup** — `pg_dump -Fc` + manifest metadata + alembic head binding | PASS（drill + CLI smoke 真实 `pg_dump -Fc` 经 `docker exec`；manifest `pg.pgDumpFormat="custom"`/`sourcePgVersion`/`alembicHead="20260812_0008"`/`sha256`/`sizeBytes`） |
| 4 | **Durable file capture** — tar/archive 内容与 PostgreSQL references 一致 | PASS（确定性 tar；drill `test_pg_and_files_consistency_after_restore` 恢复后 DB 引用文件 == 解包文件；CLI smoke `reconcile.presentCount=1/referencedCount=1`） |
| 5 | **Encryption** — AES-256-GCM envelope / per-backup DEK / versioned KEK wrapping / manifest·component·chunk AAD + sha256 | PASS（`test_envelope` round-trip/AAD-binding/wrong-DEK/tampered-chunk/tampered-manifest/chunked/empty/header-only-metadata；三层完整性：DEK-wrap tag + 每块 tag + manifest sha256；AAD = 规范 manifest 字节 + 组件名 + 块序号） |
| 6 | **Key lifecycle** — active KEK 创建新 backup / retained historical KEK 可恢复旧 backup / wrong·missing KEK fail-closed / artifact·log·repo 不泄漏 key/plaintext | PASS（`test_active_and_historical_kek_restore` 真实 PG 恢复；`test_keys` historical-unwrap/wrong-version/missing-version/tampered-wrap fail-closed；`test_no_plaintext_or_key_leakage_in_artifact` KEK 原始字节+base64 不在制品；`test_header_exposes_only_metadata` header 无 DEK/manifest） |
| 7 | **Tamper/failure negatives** — corrupted DB archive / corrupted file archive / manifest corruption / AEAD-tag failure / partial backup / encryption failure / cleanup failure | PASS（`test_corrupted_db_archive_fails_closed`→`PG_RESTORE_FAILED`；`test_corrupted_file_archive_fails_closed`→`FILES_ARCHIVE_CORRUPT`；`test_corrupted_manifest_fails_closed`→`MANIFEST_INVALID`；`test_tampered_payload_aead_auth_fails`→`ARTIFACT_AEAD_AUTH_FAILED`；pg_dump/files 失败→`INCOMPLETE`；`test_cleanup_failure_keeps_usable_artifact_with_severe_warning`→USABLE+SEVERE） |
| 8 | **Restore** — isolated empty target / 不覆盖 live / manifest+hash+AEAD 先于可用结论 / PG restore / durable files / audit hash-chain / alembic head | PASS（`assert_database_empty`+`_assert_storage_empty` 强制隔离空目标；序列：KEK lookup→DEK unwrap→payload decrypt→manifest 校验→sha256→`pg_restore`→audit chain→alembic head→extract→reconcile；drill round-trip + CLI smoke `ok=true`/`auditVerifiedRecords=1/1`/`alembic_head=20260812_0008`） |
| 9 | **File reconciliation** — orphan discard / missing referenced fail-closed | PASS（`test_orphan_file_is_discarded_during_restore`/`test_missing_referenced_file_fails_closed`/`test_unreferenced_area_orphans_discarded`） |
| 10 | **Backup state** — `USABLE` 只在完整验证成功后产生 / `INCOMPLETE` 不进 usable·restore-success 路径 | PASS（`run_backup` 仅 `primary_error is None and artifact_written` 才 `USABLE`；`test_manifest_not_usable_fails_closed`→`MANIFEST_NOT_USABLE`；INCOMPLETE artifact 删除） |
| 11 | **ADR 0027 retention/hold integration** — backupId/BACKUP_COPY identity 一致 / 不越界实现 retention cleanup | PASS（`backupId` charset `[A-Za-z0-9_-]` 长度 1–128 满足 `BACKUP_COPY` resourceId；无 BACKUP_COPY 注册/删除路径，T036 不越界 ADR 0027 predicate；`retentionClass`/`backupType` 仅元数据，不自行决定 DG-05/ADR 0009 7/4/12） |
| 12 | **Deployment boundary** — one-shot operation / 不新增 Compose service / `infra/docker-compose.yml` 不变 | PASS（`python -m risk_backup` one-shot；`docker-compose.yml` 自 `732adfb` diff 0 行；无新 service） |
| 13 | **Frozen T040/T046/OpenAPI write-sets 不变** | PASS（`scheduler.py`/`celery_app.py`/`composition.py`/`worker.py`/`main.py` 自 `732adfb` diff 0 行；`openapi.json` 自 `ac351cb` diff 0 行） |
| 14 | **不引入 WAL/PITR、cloud backup 或 DG-05 数值决策** | PASS（仅 `pg_dump -Fc`/`pg_restore`，无 `pg_basebackup`/WAL/PITR；无 cloud backend；cadence/retention 数值未决策） |

## CLI production-volume-layout smoke

repository 条件允许，执行一次接近 production volume layout 的 one-shot CLI backup → destroy isolated target → restore → integrity verification smoke（真实 PostgreSQL 16 + 真实 `docker exec pg_dump -Fc`/`pg_restore`，驱动完整 operator CLI 路径：KEK 文件加载、arg 解析、manifest、加密、恢复 fail-closed 校验）：

1. **Setup** — 创建专用源 DB（Alembic `upgrade head` 至 `20260812_0008`）+ seed 真实 backup set（user→durable task→import batch + audit hash-chain entry + durable storage file）+ 生成 base64 32-bit KEK 文件；创建隔离空目标 DB（未迁移，由 `pg_restore` 带入 schema+data）。
2. **`python -m risk_backup backup`** — `status=USABLE`，manifest 含 `backupId`/`alembicHead=20260812_0008`/`kekKeyVersion=v1`（仅版本）/组件 sha256+size；`pg_dump -Fc` 192985 bytes、file tar 10240 bytes；明文 temp 目录已清理。
3. **`python -m risk_backup restore`** — `ok=true`，`auditTotalRecords=1`/`auditVerifiedRecords=1`（哈希链校验通过），`reconcile.presentCount=1/referencedCount=1/missingCount=0/orphansRemoved=0`，`backupId` 与 backup 一致，`kekKeyVersion=v1`。
4. **Verify** — 目标 DB `users=1`/`durable_tasks=1`/`import_batches=1`，`alembic_version=20260812_0008`，audit 全部含 `integrityHash`（链完整），durable 文件内容逐字节匹配 `SMOKE-XLSX-PRODUCTION-VOLUME-CONTENT` → **PASS**。
5. **Teardown** — 源/目标 DB drop。

## Integration fix

| 项 | 内容 |
|---|---|
| 根因 | CLI `cmd_restore` 调用 `_build_pg_dumper(args)` 读取 `args.dsn`，但 restore 子解析器仅定义 `--target-dsn`（无 `--dsn`）→ `AttributeError`。backup/restore 单元测试与 PG drill 直接调用 `run_backup`/`run_restore`，从未驱动 CLI 子命令，故未覆盖。 |
| 修复 | `infra/backup/src/risk_backup/cli.py`：`_build_pg_dumper(args, dsn)` 显式接收 DSN；`cmd_backup` 传 `args.dsn`，`cmd_restore` 传 `args.target_dsn`（+4/−4）。 |
| 回归测试 | 新增 `infra/backup/tests/test_cli.py`（2 tests）：backup CLI 从 `--dsn` 构建 dumper（目标=源 DB）、restore CLI 从 `--target-dsn` 构建 dumper（目标=隔离目标 DB）。 |
| 边界 | 仅 `infra/backup/**`；未触碰 frozen write-set / `docker-compose.yml` / OpenAPI authority / app 代码。`risk_platform` 不 import `risk_backup`（隔离）。 |

## Independent Review non-blocking observations 评估

5 项均确认为已批准/非阻断行为，不升级为 integration blocker，不扩 scope 修改：

1. **quiesce/full-stop semantics** — `ComposeQuiescer` 停 api/worker/scheduler（非 ADR §2 maintenance 读态）。因实现 maintenance 态需改 frozen API write-set（ADR §11/§12 禁止），full stop 是**严格更强**的 quiesce，一致性不变式仍保证。`非阻断`。
2. **backupId hash-source circularity** — `compute_backup_id` 哈希 `{type}:{alembic_head}:{pg_sha256}:{files_sha256}`（manifest 绑定内容）而非字面 manifest-sha256，解开 backupId-in-manifest 循环；确定性/可排序/唯一保留。`非阻断`。
3. **SEVERE cleanup exit-code** — `cmd_backup` 在 USABLE+`PLAINTEXT_CLEANUP_FAILED` 时返回 0（ADR §9 允许此情形 USABLE）。为运维加固建议（automation flag），非 correctness/security defect；unquiesce_warning 路径已返回 1。`非阻断`。
4. **reconcile schema boundary** — 引用源仅 `import_batches.storageKey`。已核验全 schema：唯一 durable-file 引用列为 `import_batches.storageKey`（VARCHAR 500）；`sourceKey`/`importKey`/`matchedImportKey` 为 64-char 身份/哈希 key 非 file path；mail 附件经 ADR 0024 in-memory 解析、不落盘。与 ADR §1 文件存储范围（import 源）一致。forward-looking caution，当前 ADR scope 内 `非阻断`。
5. **outer-header structural metadata** — header 携带 aead/chunkMaxBytes/components 超出 §4 字面列举，但均为解析容器所需非敏感格式字段，无密钥/明文（`test_header_exposes_only_metadata` 断言）。cosmetic。`非阻断`。

## Validation

| # | 验证项 | 结果 |
|---|---|---|
| 1 | focused backup/restore tests + full `infra/backup/**` suite | PASS（`61 passed`：59 原有 + 2 新 CLI 回归） |
| 2 | real PostgreSQL 16 round-trip backup/restore drill | PASS（`8 passed`：round-trip/PG+files 一致性/orphan discard/missing referenced/corrupted DB/corrupted file/active+historical KEK/broken audit chain） |
| 3 | real durable-volume file round-trip | PASS（drill + CLI smoke file 内容逐字节匹配） |
| 4 | active + historical KEK restore | PASS（`test_active_and_historical_kek_restore` 真实 PG；v1 备份以 v2 active+v1 retained 恢复） |
| 5 | tamper/fail-closed matrix | PASS（wrong-DEK/tampered-chunk/tampered-manifest/wrong-magic/truncated/missing-KEK/tampered-wrap/sha256-mismatch/manifest-invalid/not-usable 全 fail-closed） |
| 6 | audit hash-chain negative | PASS（`test_broken_audit_hash_chain_fails_closed`→`AUDIT_CHAIN_BROKEN`） |
| 7 | cleanup success/failure paths | PASS（success 清理 temp；`PLAINTEXT_CLEANUP_FAILED`→USABLE+SEVERE；`UNQUIESCE_FAILED`→USABLE+warning） |
| 8 | CLI production-volume smoke（backup→destroy target→restore→integrity） | PASS（见上） |
| 9 | Ruff（backup src+tests） | PASS（`All checks passed!`） |
| 10 | mypy strict（backup，28 source files） | PASS（`Success: no issues found in 28 source files`） |
| 11 | Ruff（app src+tests） | PASS（`All checks passed!`；cli.py 不在 app mypy scope，app 不 import risk_backup） |
| 12 | mypy（app，187 source files） | PASS（`Success: no issues found in 187 source files`） |
| 13 | full pytest（PostgreSQL 16 + Redis 7） | PASS（`303 passed, 1 skipped` + 1 既有 flaky timing test `test_slow_tool_obeys_attempt_deadline_heartbeat_and_cancellation` 在全量负载下 heartbeat 计数 2<3，**单独重跑 PASS**；属 frozen T029/T040 agent-execution 时序敏感测试，与 T036 无关，非 integration blocker） |
| 14 | `uv lock --check` | PASS（`Resolved 65 packages`） |
| 15 | `git diff --check` | PASS（clean） |
| 16 | frozen write-set / `docker-compose.yml` / OpenAPI 不变 | PASS（scheduler/celery_app/composition/worker/main 自 `732adfb` diff 0；docker-compose diff 0；openapi.json 自 `ac351cb` diff 0） |
| 17 | write-set 合规 | PASS（仅 `infra/backup/**`：`cli.py` +4/−4、新增 `tests/test_cli.py`） |

## Wave 24 / T037 readiness 同步（仅 metadata，未执行 T037、未启动 Wave 24）

T037（deps T033/T034/T036）的 dependency 层现已全部满足——T033/T034/T036 均 `REVIEW_PASSED` + Wave 19/20/23 Integration `PASS`。T037 inherited design gaps 均已解决：DG-04（ADR 0027）、DG-08（ADR 0031）、DG-10（ADR 0022）；DG-05（capacity thresholds）为 T037 `Explicit out-of-scope`（属 T038）。T037 状态由 `BLOCKED_DESIGN_GAP (inherits DG-04/DG-05/DG-08/DG-10) / TODO` 同步为 `READY`（仅 metadata，纠正 stale header），可在 Wave 24 评估/执行。本次不执行 T037、不启动 Wave 24；DG-05 保持 out of scope。

## 停止边界（按本次授权与协议）

- **已执行** Wave 23 Integration（含 1 项 `infra/backup/**` write-set 内最小 integration fix + 回归测试）并创建 final checkpoint。
- **未执行** T037。
- **未启动** Wave 24。
- **未处理** DG-05。
- **未修改** `infra/docker-compose.yml` 或任何 frozen write-set（T035/T040/T046）或 frozen OpenAPI authority。
- 备份/恢复为 one-shot 命令，未新增 application entrypoint、未注册 executor、未改 composition（ADR 0031 §11/§12）。
