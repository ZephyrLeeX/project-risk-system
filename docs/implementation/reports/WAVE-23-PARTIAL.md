# Wave 23 — Partial（T036 encrypted backup/restore runbook）

- **Wave:** 23
- **Tasks:** T036（单一 work unit；ADR 0031 / DG-08 resolution 引入）
- **状态:** `IN_PROGRESS`（partial — T036 `REVIEW_PASSED` + code checkpoint；Wave 23 Integration 未启动）
- **Wave 23 Integration:** 未启动
- **下一 Wave（Wave 24 / T037）/ DG-05:** 未启动 / 未处理

## 背景

T036 原为 DG-08 阻塞（备份加密/密钥/一致性契约缺失）。ADR 0031 解决 DG-08（design/metadata checkpoint `2796971de280ff44b77d8ba1f51565f60fa675e1`，未实施 production code、未写 `infra/backup/**`、未启动 Wave 23）：批准 quiesce 协调的一致备份集合、AES-256-GCM 信封加密（复用 T007 `rpenc`/`KeyRing`）、manifest 绑定、版本化备份 KEK + 保留历史可恢复性、fail-closed 隔离恢复、metadata-only 日志、`backupId` 作为 ADR 0027 `BACKUP_COPY` 标识；备份/恢复为 one-shot 命令，不重开 T035 Compose。T036 由 `DESIGN_GAP (DG-08)` 恢复为 `READY`（仅 metadata）。

Wave 22 Integration `PASS` 后 T036 dependency 层（T031、T035）全部满足 → Wave 23 标记 `IN_PROGRESS`，仅授权 T036（Lean Execution Mode）。

## Readiness

- T036 blocking 前置 T031（`REVIEW_PASSED`）、T035（`REVIEW_PASSED`）+ Wave 22 Integration `PASS` 已满足；ADR 0031 提供批准的备份/恢复契约。无新 blocker → Wave 23 标记 `IN_PROGRESS`，仅授权 T036。
- Lean Execution Mode 加载：T036 Task、ADR 0031、ADR 0008/0009/0014/0027、T007 `rpenc`/`KeyRing` 契约、T035 volume 布局、T031 存储布局。

## T036 结果：`REVIEW_PASSED`

新增 `infra/backup/**`（`risk_backup` package：`envelope`/`keys`/`backup`/`restore`/`manifest`/`archive`/`reconcile`/`db`/`pgdump`/`quiesce`/`temputil`/`timeutil`/`cli`/`errors` + 11 test files + README + pyproject/conftest）。

- **加密**：AES-256-GCM 信封；per-backup 随机 256-bit DEK 被 versioned 备份 KEK 经 T007 `rpenc` 包装（KEK 版本作 AAD）；三层完整性（DEK-wrap tag + 每块 tag + manifest sha256）；数据组件 AAD 绑定规范 manifest 字节 + 组件名 + 块序号；nonce 由 per-component 随机基 XOR 块序号派生，无 (key,nonce) 复用。
- **密钥**：256-bit 备份 KEK 从宿主只读文件加载（不从 env），独立于 `DATA_ENCRYPTION_KEY`；active + retained 版本；历史备份不 re-encrypt 即可恢复；制品仅含包装后 DEK + KEK 版本。
- **备份一致性**：quiesce 协调捕获（quiesce → confirm → `pg_dump -Fc` → tar durable files → manifest → encrypt → cleanup → unquiesce）；任一失败 → `INCOMPLETE`（删除部分制品）；`USABLE` 仅当完整加密/hash/manifest 校验通过；明文临时目录与输出分离，成功+失败均清理，清理失败 → USABLE + SEVERE warning。
- **恢复 fail-closed**：隔离空目标（不覆盖在线）；密钥查找（`MISSING_KEK_VERSION`）→ AEAD 校验（`ARTIFACT_AEAD_AUTH_FAILED`）→ manifest 校验 → 组件 sha256 → `pg_restore` + 审计哈希链校验（`AUDIT_CHAIN_BROKEN`）+ alembic head → 文件 extract + reconcile（丢弃 orphan，`RESTORE_MISSING_REFERENCED_FILE`）；部分集合中止。
- **范围/边界**：备份权威 = PostgreSQL + durable 文件存储；Redis/Celery/cache/temp/log 排除；密钥材料不入备份；metadata-only 日志（KEK 仅版本），不写业务审计链；one-shot 命令，不新增 Compose service，未改 `docker-compose.yml`/frozen write-set，无 WAL/PITR/`pg_basebackup`。

Independent Review `REVIEW_PASSED`，无 blocking finding（5 项 non-blocking observation，详见 `docs/implementation/reports/T036.md`）。

## Validation（全部 PASS）

- focused unit tests：`51 passed`
- real PostgreSQL 16 backup/restore drill：`8 passed`
- 全量 backup tests：`59 passed`
- Ruff：`All checks passed!`
- mypy strict：`Success: no issues found in 27 source files`
- `uv lock --check`：`Resolved 65 packages`
- `git diff --check`：clean
- write-set 合规：仅 `infra/backup/**`；`infra/docker-compose.yml` 与全部 frozen write-set 未修改

## code checkpoint

T036 code checkpoint `5824695f73c1c31e272b17e88324e13b674b80d1`（metadata 由后续提交补录于 `EXECUTION_STATE.md` / `T036.md`）。

## 未执行项（按本次授权与协议边界）

- Wave 23 Integration：**未启动**。
- Wave 24 / T037（compatibility/security suite）：**未启动**。
- DG-05（performance/reliability numeric thresholds）：**未处理**。
- frozen write-sets（T035 `infra/docker-compose.yml`、T040 `celery_app.py`/`composition.py`/`worker.py`/`main.py`、T046 `scheduler.py`）与 frozen OpenAPI authority：**未修改**。
- 备份调度（每日/周/月触发）：属运维流程（宿主 cron 或手动），非本架构决策；本 Wave 未实现调度自动化。
