# 定义加密备份与恢复契约

状态：已批准

## Context

ADR 0009 要求每日对 PostgreSQL 与文件存储做加密备份，保留 7 个日版本、4 个周版本和 12 个月版本，
RPO 24 小时、RTO 4 小时；并规定只有实际恢复并核对数据、文件和审计链后，备份才视为有效。ADR 0027
为备份副本定义了 `BACKUP_COPY` 保全类型与确定性删除资格 predicate，并明确把“备份加密、密钥格式、
备份清单、PostgreSQL/文件一致性机制”留给 DG-08。ADR 0014 已批准版本化密钥注入与“仓库/镜像/日志/
审计/API 不得含完整密钥”的边界，T007 已交付可复用的 `rpenc` AES-256-GCM 版本化密钥环
（`risk_platform.shared.crypto`）。但现有 ADR 未选择一个独立 Agent 可直接实现、可验证、fail-closed
的备份/恢复机制：未规定 PostgreSQL 与文件如何形成一个一致可恢复集合、未规定加密容器与清单格式、
未规定密钥来源/版本/轮换/退役、未规定恢复校验与失败语义。因此 T036 无法安全实现。

本 ADR 只解决 DG-08。它不实现备份/恢复脚本，不选择生产备份目标/演练窗口，不引入 WAL 归档/热备/HA
（T036 明确 out-of-scope：WAL HA），不决定 DG-05 的数值性能/可靠性阈值，不修改 T035/T040/T046/T031
的冻结写集，不重开 T035 的 Compose/proxy/env 写集。ADR 0009 的 7/4/12 保留数与 RPO/RTO 基线保持不变，
本 ADR 不自行设定任何数值 SLO。

## Decision

### 1. 备份权威与范围

备份权威（backup authority）= PostgreSQL（唯一业务数据库权威）+ 应用文件存储（durable application
file storage）。二者构成一个不可拆分的 **备份集合（backup set）**。

- **PostgreSQL 备份包含**：全部 schema 与数据——业务表、审计哈希链（ADR 0008）、durable task/outbox
  事实与游标（ADR 0018/0022）、`system_config_releases`（含 `retention` 配置，ADR 0027）、
  `retention_holds`、已加密的应用凭据密文（`rpenc` envelope，仅含密文与密钥版本，不含明文密钥）、
  邮箱同步状态/cursor 等。即 PostgreSQL 中的全部持久业务事实。
- **应用文件存储备份包含**：`project-risk-storage` 命名卷（容器内 `/app/storage`）下的 durable 文件，
  主要是导入原文件（`IMPORT_STORAGE_DIR=/app/storage/excel`，T018/T019）。
- **明确不进入备份**：
  - Redis（仅 broker，`--save "" --appendonly no`，非事实源，ADR 0006/0030；其状态由 scheduler
    `reconcile` 恢复）；
  - Celery 队列/消息/运行态/lease（ADR 0018，broker-loss 可恢复）；
  - 运行态缓存、进程内存、liveness 探针状态；
  - 临时/scratch 目录与 orphan temp 文件（T031 清理目标、T017/T025 解析临时产物，ADR 0007/0024；
    非 durable，可能含 partial/orphan 内容）；
  - 容器 stdout 日志与宿主运行日志（运维观测数据，非业务恢复权威；审计已在 PostgreSQL 内，由 DB
    备份覆盖）。
- **密钥材料不进入备份**：应用加密密钥（`DATA_ENCRYPTION_KEY` 及其保留版本）与本 ADR 定义的备份密钥
  （见第 5 节）均为宿主运维密钥，**绝不**写入备份制品、仓库、镜像或日志。备份只含密文与其密钥版本。

> ADR 0003 提及“日志纳入备份恢复方案”：本 ADR 将其落为“审计链（在 PostgreSQL 内）+ 业务文件”构成
> 恢复权威；运维容器/宿主日志不属于业务恢复权威，不作为 DG-08 备份目标。此范围与 ADR 0009“PostgreSQL
> 加密备份和文件存储同步备份”一致。

### 2. PostgreSQL + 文件一致性模型

**禁止**仅分别复制 PostgreSQL 与文件、随后声称二者事务一致。备份集合必须经 **quiesce 协调捕获
（quiesce-coordinated capture）** 产生，形成一个可恢复的一致点。

**Quiesce 与捕获序列**（备份命令编排，全部在一个备份作业内完成）：

1. **Quiesce 写路径**：优雅停止 `scheduler` 与 `worker` 容器（SIGTERM，令当前 tick/handler 收尾），
   并将 API 置于 **备份维护态**——拒绝所有 mutating 请求（统一 `503` + 固定 maintenance code），
   读请求可继续。此期间无后台清理（T031）、无 mailbox sync 产生的新 work、无新导入写。
2. **确认 quiesce**：备份命令必须确认 scheduler/worker 已退出、API 已进入 maintenance。任一确认失败
   → **fail-closed**，不产出 usable 备份。
3. **PostgreSQL 一致捕获**：对 `postgres` 执行 `pg_dump`（单快照，逻辑转储；`pg_dump` 内部以单一
   transaction snapshot 保证 DB 自身一致，与是否 quiesce 无关）。因写路径已 quiesce，该快照亦与文件态
   对齐。**不使用** `pg_basebackup`/WAL 归档/PITR（T036 out-of-scope：WAL HA）。
4. **文件捕获**：在 DB 快照之后 tar 归档 `project-risk-storage` 卷的 durable 内容（排除临时/scratch
   子目录）。因无清理/回滚在窗口内运行，DB@快照引用的文件必然仍在盘；窗口内不可能产生新 durable
   文件（API maintenance、worker/scheduler 停）。故文件归档 ⊇ DB@快照所需引用，绝不缺文件。
5. **清单**：写 manifest 绑定两组件（见第 3、7 节）。
6. **加密**：按第 4 节加密备份集合。
7. **Unquiesce**：恢复 worker/scheduler、解除 API maintenance。

**恢复时一致化（reconcile）**：将文件归档解包到隔离目标后，**丢弃**任何被恢复 DB 不引用的 orphan
文件（防御性，处理理论上的多余文件），并校验 DB 引用的文件均存在。缺失或多余且不可解释 → fail-closed。
该 reconcile 与 quiesce 捕获共同保证恢复集合一致，且不依赖“分别复制恰好对齐”。

> 单机内网、RPO 24 小时（每日备份）下，quiesce 窗口为分钟级，运维可接受。这是不引入 PITR 的前提下
> 唯一可验证、fail-closed 的一致化方式。

### 3. 备份格式

- **PostgreSQL 组件**：`pg_dump` custom-format 二进制转储（`-Fc`，单一致快照，含全部 schema/对象），
  文件名 `<backup-id>.pgdump`。恢复用 `pg_restore` 进入隔离空库。
- **文件组件**：durable 文件存储的 tar 归档（保留路径、权限），文件名 `<backup-id>.files.tar`；
  显式排除临时/scratch 子目录（T036 依 T031/T017/T025 存储布局以读依赖确定 exclude 清单）。
- **manifest（清单）**：JSON，作为加密 payload 的一部分（见第 4 节），**随备份集合一同加密**，提供
  tamper-evident 完整性。外层只暴露格式魔数与备份密钥版本（用于密钥查找，非敏感）。必含字段：
  - `manifestFormatVersion`（固定，本版 `v1`）；
  - `backupId`（见第 7 节，亦为 ADR 0027 `BACKUP_COPY` 稳定标识）；
  - `backupType`：`daily` | `weekly` | `monthly`；
  - `createdAt`：UTC RFC 3339 毫秒；
  - `pg`：`{file, pgDumpFormat, sourcePgVersion, alembicHead, schemaHash?, sizeBytes, sha256}`；
  - `files`：`{file, rootPath, entryCount, sizeBytes, sha256, excludes}`；
  - `encryption`：`{algorithm, aead, kekKeyVersion, wrapEnvelopeFormat, payloadNonceRef}`（**不含**密钥
    材料、不含 DEK 明文/密文）；
  - `retentionClass`：`daily`|`weekly`|`monthly`（与 `backupType` 对应，仅元数据；不自行决定 ADR 0009
    数值）；
  - `status`：`USABLE` | `INCOMPLETE`（见第 9 节，只有完整加密校验通过才置 `USABLE`）；
  - `createdBy`/`traceId`（运维元数据，不含敏感内容）。

### 4. 加密

- **算法**：AES-256-GCM（与 T007 `rpenc` 同一 AEAD 原语），authenticated encryption，提供机密性 +
  完整性 + 篡改检出。
- **信封加密（envelope encryption）**：每份备份生成一个随机 256-bit **DEK**（data-encryption key）；
  备份 payload（manifest + pgdump 组件 + files 组件）用 DEK 经 AES-256-GCM 加密；DEK 用 **备份 KEK**
  （key-encryption key，见第 5 节）经 `rpenc` AES-256-GCM envelope 包装（携带 KEK 密钥版本作为 AAD，
  复用 T007 格式与语义）。外层头仅含：格式魔数/版本、KEK 密钥版本、包装后的 DEK envelope。
- **完整性语义**：DEK-wrap 的 GCM tag 验证 + payload 的 GCM tag 验证 + manifest 内组件 sha256 验证
  三层校验。任一失败 → fail-closed，不得部分恢复。
- **AAD**：payload 加密的 AAD 至少绑定 `manifestFormatVersion`、`backupId`、`createdAt`、组件文件名与
  sha256 摘要（即 manifest 自身），防止组件替换/重排。
- **明文临时文件**：允许在备份作业执行期间于受控临时目录存在明文（pgdump 产物、tar、待加密 payload）；
  其生命周期**仅限单次备份作业**，作业结束（无论成功/失败）必须清理。明文临时目录与加密制品输出目录
  必须分离，且明文目录不得位于备份输出路径下。明文清理失败不使已加密制品失效，但触发 SEVERE 安全
  告警（见第 9 节）。明文不得写入仓库/镜像/日志。
- **大文件**：payload 可按需采用分块 AEAD（每块独立 AES-GCM，块序号入 AAD，nonce 由 KEK/DEK 派生
  计数器，禁止 (key, nonce) 复用）；manifest `encryption` 字段记录所用变体。具体分块细节由 T036 实现，
  但必须保持 authenticated-encryption + 完整性语义。

### 5. 密钥管理

- **密钥来源**：备份 KEK 为 256-bit 密钥，从**宿主只读密钥文件**加载（Docker Secret 或只读挂载文件），
  与 T007 `KeyRing.from_files` 同模型；**不从进程环境变量读取**（与 `rpenc` 一致）。备份 KEK 与应用
  `DATA_ENCRYPTION_KEY` **相互独立**的密钥材料，使备份密钥轮换与应用凭据密钥轮换解耦。
- **密钥标识/版本**：复用 T007 密钥版本语义——`KeyRing`（一个 active 加密版本 + 若干 retained decrypt
  版本），版本标识符匹配 `[A-Za-z0-9_-]{1,32}`。新备份用 active 版本；历史备份用其创建时记录的版本解密。
- **存储边界**：KEK 文件仅存于宿主运维目录，gitignored，绝不进仓库/镜像/备份制品/日志。备份制品只含
  **包装后的 DEK** + **KEK 密钥版本**，绝不含 KEK 本身、不含 DEK 明文。
- **Compose/宿主注入**：备份作业为 one-shot 命令（见第 12 节），运行时以命令行 `-v`/`--env` 注入备份
  KEK 只读文件与目标路径；不修改 T035 Compose、不为备份新增 compose service 或 secret 项。KEK 注入路径
  与 session key / `DATA_ENCRYPTION_KEY` 同样遵守 ADR 0014“只读密钥文件注入”边界。
- **禁止**：将 KEK/DEK 写入仓库、备份制品、镜像或日志（ADR 0014）。

### 6. 密钥轮换

- **新备份**：使用当前 active 备份 KEK 版本。
- **历史备份可恢复性**：保留对应历史版本的 retained decrypt KEK，历史备份**不需要任何转换**即可解密。
- **不 re-encrypt 历史备份**：轮换不回溯重加密历史制品（避免 decrypt+re-encrypt 风险与不必要操作）。
- **退役条件**：某 KEK 版本仅在以下条件**全部**满足后方可退役/销毁——所有用该版本加密的备份均已按
  ADR 0009 超出 7/4/12 保留窗口、且未被有效 `BACKUP_COPY` hold 保护（ADR 0027）、且无未完成恢复演练
  引用。退役为显式运维操作，记录于运维元数据日志（见第 10 节）。退役后保留其曾保护备份的最小审计痕迹
  （仅版本号与时间，不含密钥）。

### 7. 备份命名 / 保留元数据

- **backupId**：稳定、确定、可排序、唯一，字符集限于 `[A-Za-z0-9_-]`，长度 1–128（满足 ADR 0027
  `BACKUP_COPY` resourceId 约束）。格式：
  `<backupType>-<UTC YYYYMMDDTHHMMSSZ>-<manifest-sha256 前 8 hex>`，例 `daily-20260815T021500Z-a1b2c3d4`。
- **与既有保留策略的边界**：本 ADR **不**自行决定 DG-05 的数值 SLO，也**不**放宽/新增 ADR 0009 的 7/4/12
  数值或 ADR 0027 的删除资格 predicate。backupId 仅为 ADR 0027 `BACKUP_COPY` 提供稳定标识；副本是否
  `ELIGIBLE` 删除仍由 ADR 0027 predicate（超出保留集合 + 无有效 hold + 无未完成演练）判定，T036 在删除
  前以同一 `asOf` 复核并写 `BACKUP_COPY_DELETED`/失败审计。`backupType`/`retentionClass` 仅元数据，
  保留调度仍由 ADR 0009 固定策略驱动。

### 8. 恢复契约（fail-closed）

恢复目标默认为**隔离空目标**（独立 DB + 独立存储目录），**不覆盖在线系统**（T036 acceptance）。
恢复序列：

1. **密钥查找**：读外层头 → KEK 密钥版本 → 宿主密钥库查找该版本；**未找到 → fail-closed**，中止，不
   部分恢复。
2. **完整性校验**：解包 DEK（GCM tag 校验）→ 解密 payload（GCM tag 校验）→ 解析 manifest；任一 AEAD
   tag 无效 → **fail-closed**（篡改/损坏）。校验各组件 sha256 与 manifest 一致 → 不一致 → fail-closed。
3. **manifest 校验**：`manifestFormatVersion` 受支持、`status == USABLE`、组件齐全（pg + files + manifest）、
   `alembicHead`/schema 版本可识别。缺失/不匹配 → fail-closed。
4. **PostgreSQL 恢复**：`pg_restore` 进入隔离空库；执行审计哈希链完整性校验（ADR 0008）；断链 → fail-closed。
5. **文件恢复**：解包 files.tar 到隔离目标存储根；reconcile——丢弃 DB 不引用的 orphan 文件，校验 DB
   引用文件均存在；不一致 → fail-closed。
6. **部分/不匹配集合必须 fail-closed**：PG 组件 OK 但 files 缺失/损坏（或反之）→ 整体中止，不得将部分
   恢复声称为成功。
7. **演练**：恢复到隔离目标后，核对 DB/文件/配置关联 + 审计链，测量并记录 RTO；在线系统不受影响。完整
   系统可用还需宿主提供应用加密密钥环（`DATA_ENCRYPTION_KEY` 及保留版本），此为运维密钥，不在备份内。

### 9. 失败语义

- **DB 成功但 files 失败**（或反之）→ 备份集合 `INCOMPLETE`，**不**置 `USABLE`，不注册为可用副本
  （ADR 0027 hold 不保护非 usable 制品）；尽力清理部分产物，记录失败。
- **加密失败** → 备份失败；清理一切明文临时文件；无 `USABLE` 制品；记录。
- **manifest 失败** → 备份 `INCOMPLETE`；清理；记录。
- **清理失败（明文临时文件未清）** → 已加密制品若完整可仍 `USABLE`，但触发 **SEVERE 安全告警**（明文
  残留风险）；runbook 要求运维人工核查/清理临时目录后方可视为成功。
- **quiesce 确认失败** → fail-closed，不产出 usable 备份。
- **不完整备份不得标记为 usable**：只有全部组件齐备、加密完成、hash/manifest 校验通过、`status=USABLE`
  的备份才视为可用。`USABLE` 不等于“已验证可恢复”——ADR 0009 要求实际恢复演练后才视为有效备份；
  `USABLE` 仅表示制品结构完整、可进入恢复流程。

### 10. 审计与日志

- 备份/恢复为 **metadata-only** 运维日志：记录 `backupId`、`backupType`、时间、KEK 密钥版本（**仅版本**，
  非密钥）、组件名/大小/sha256、`status`、操作者、测量 RTO、错误 code。
- **绝不**记录：KEK/DEK 密钥材料、明文内容、文件内容、DB dump 内容、邮件内容、凭据、邮件正文/附件
  （ADR 0007/0014）。
- **PostgreSQL 业务审计链**：仅 ADR 0027 已批准的 `BACKUP_COPY_DELETED`/失败审计（副本删除）写入业务
  审计链。备份创建、恢复、密钥退役为运维事件，记录于运维元数据日志，**不**写入 PostgreSQL 业务审计链
  （备份作业不构造 domain service、不写业务 audit，ADR 0017）。

### 11. 归属与写集

- **T036 直接拥有 backup + restore 实现**：加密/解密、manifest、quiesce 编排、`pg_dump`/`pg_restore`
  调用、文件归档/reconcile、完整性校验、失败语义、drill runbook、tests，全部位于
  `project-risk-system/infra/backup/**` + 专属 backup tests/runbook（T036 expected write set）。
- **读依赖（不修改）**：复用 `risk_platform.shared.crypto`（T007 `rpenc`/`KeyRing` 格式与语义）包装 DEK；
  依 T035 volume 布局（`project-risk-postgres-data`、`project-risk-storage`）、T031 存储布局确定
  durable/临时目录边界；依 ADR 0027 predicate 复核副本删除资格。
- **无需新增 application entrypoint / remediation Task**：备份为 offline one-shot，不调用运行中 app API、
  不写业务 audit、不注册 executor、不改 composition。DAG 不因归属变更；T036 依赖 T031/T035 不变。

### 12. 部署关系

- T035 Compose 已完成且冻结。**备份/恢复为 one-shot 命令 / 宿主运维作业**，**非**长驻 Compose service。
- 执行方式：`docker compose run --rm`（或等价 `docker run`）复用**既有** image——`postgres:16-alpine`
  提供 `pg_dump`/`pg_restore`，`risk-platform-api:0.1.0`（含 `cryptography` + `risk_platform.shared.crypto`）
  提供加密/文件归档/manifest；备份 KEK 与输出目标以命令行参数/挂载注入。
- **不重开 T035**：不新增 compose service、不编辑 `infra/docker-compose.yml`/proxy/env-example、不改
  T040 composition 或 T046 scheduler。若未来需要将备份纳为定时 compose service，需另行 ADR；本 ADR 不
  授权。备份调度（每日/周/月触发）由宿主 cron 或运维手动驱动，属运维流程而非本架构决策。

## Consequences

- T036 获得一个可独立实现、可验证、fail-closed 的加密备份/恢复契约：quiesce 协调的一致备份集合、
  AES-256-GCM 信封加密（复用 T007 密钥环语义）、manifest 绑定、版本化 KEK 与保留历史可恢复性、隔离
  恢复 + 三层完整性校验 + 部分集合 fail-closed。
- DG-08 由本 ADR 解决；ADR 0027 `BACKUP_COPY` 的稳定标识 = 本 ADR `backupId`，副本删除 predicate 与
  `BACKUP_COPY_DELETED` 审计保持由 ADR 0027 拥有，不重开。
- T036 由 `DESIGN_GAP (DG-08)` 恢复为 `READY`（仅 metadata，未实施）。DG-05 数值阈值、T035/T040/T046
  冻结写集、`infra/backup/**` 实现均未触碰；Wave 23 未启动。
- 本 ADR 不实现任何备份/恢复脚本、不修改 production code、不创建 code checkpoint。
