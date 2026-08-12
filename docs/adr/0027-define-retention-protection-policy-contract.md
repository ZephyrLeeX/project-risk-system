# 定义留存与保护策略契约

状态：已批准

## Context

ADR 0012 确定导入原文件默认保留一年、Agent 完整对话默认保留 90 天，并要求回滚窗口和审计保全中的
内容不得自动删除。ADR 0009 又规定备份保留 7 个日版本、4 个周版本和 12 个月版本。它们没有定义配置
的允许范围、回滚保护的起止和持久化事实、保全状态或清理与备份轮转所依赖的确定性判定。因此 T042、
T031 以及后续备份工作不能安全实现。

本 ADR 只解决 DG-04。它不选择备份加密、密钥格式、备份清单或 PostgreSQL/文件一致性机制（DG-08），
不改变 T019 已有的导入回滚行为，不实现删除任务，也不增加 Agent 的写工具。

## Decision

### 留存配置与版本边界

正式系统在既有 `system_config_releases.snapshot` 中新增严格的 `retention` 对象，并新增唯一的配置模块
`RETENTION`。它随既有 `/admin/system-config/publish` 原子发布、产生既有 release `version`，且只能由已有
`admin.config.manage` 权限发布；这是对既有响应/快照的加性扩展，不改变其 URL、envelope 或已有字段含义。

`retention` 只能含有下列整数天数，采用 UTC 时长（一天固定为 24 小时），边界均包含：

| 配置项 | 默认值 | 允许范围 | 含义 |
|---|---:|---:|---|
| `importSourceRetentionDays` | 365 | 30–730 | 导入原文件自上传完成时起的留存期 |
| `agentConversationRetentionDays` | 90 | 30–365 | 完整 Agent 会话自创建时起的留存期 |
| `importRollbackProtectionDays` | 30 | 7–90 | 已成功导入批次原文件的额外回滚保护期 |

ADR 0009 的备份轮转数 `7` 个日版本、`4` 个周版本、`12` 个月版本保持固定的部署/备份策略，而不是上述
管理员配置项；不得通过 T042 放宽、缩短或新增其范围。备份机制本身仍由 DG-08 决定。

一次发布不得追溯改变已创建内容的到期时刻。T042 必须在以下写入的同一事务中写入计算结果和所使用的
release `version`：

- `ImportBatch` 增加 `sourceExpiresAt`、`rollbackProtectedUntil` 和 `retentionConfigVersion`。原文件上传
  成功时，`sourceExpiresAt = createdAt + importSourceRetentionDays`；该批次确认导入成功时，
  `rollbackProtectedUntil = confirmedAt + importRollbackProtectionDays`。未确认、失败或已回滚批次的
  `rollbackProtectedUntil` 为 `NULL`。
- `AgentConversation.expiresAt` 是会话的已冻结到期事实：创建时按
  `createdAt + agentConversationRetentionDays` 写入。T028 不得在读取时用当前配置重新计算它。会话不需要
  再增加重复的到期列，但 T042 的 revision 必须增加并由创建路径持久化
  `AgentConversation.retentionConfigVersion`，以便审计和排错。

T042 的一次串行 Alembic revision 负责上述增量列、索引及下述 hold 表。新字段上线时已存在的导入批次
使用迁移时的已发布 `retention` 配置；若尚无 release，使用本 ADR 的默认值，并从已有 UTC
`createdAt`/`confirmedAt` 回填。回填不得重写原文件、业务记录或审计链。字段缺失、`NULL`、未知配置版本
或非 UTC 时间均为 fail-closed：清理不得删除。

`importRollbackProtectionDays` 仅定义原文件的留存保护窗口，不改变 T019 对
`POST /api/imports/batches/{batch_id}/rollback` 的授权、冲突检测或可调用期限；T019 的业务快照是回滚依据，
不是原文件的清理许可。

### 审计保全状态

T042 新增 `retention_holds`，作为独立的、可审计的保护事实。表至少含：UUID `id`、封闭枚举
`resourceType`（`IMPORT_BATCH`、`AGENT_CONVERSATION`、`BACKUP_COPY`）、稳定 `resourceId`、封闭枚举
`reason`（`LEGAL`、`INVESTIGATION`、`INCIDENT`、`RESTORE_DRILL`）、状态、`createdAt`/`createdById`/
`createdTraceId`、可空 UTC `expiresAt`，以及 release/expiry 的 actor、trace 和时间。不得存入文件名、
文件内容、对话正文、自由文本原因、备份内容或任意 JSON metadata。

状态机是 `ACTIVE -> RELEASED` 或 `ACTIVE -> EXPIRED`，终态不可重新激活；需要再次保全时必须建立新的
hold。`ACTIVE` hold 的 `expiresAt` 可为 `NULL`；当 `asOf >= expiresAt` 时，它在保护判定中已失效，且
清理任务必须先在同一事务将其置为 `EXPIRED` 并写审计，再继续判定。`RELEASED` 必须由明确操作写入，
且保留历史行，禁止删除或覆盖创建事实。对同一 `(resourceType, resourceId)` 最多允许一个有效的
`ACTIVE` hold（含尚未到期者）；建立、释放、到期转换都以锁定该资源/hold 行的事务完成，避免与清理竞争。

建立或释放 hold 只允许已有 `admin.config.manage` 权限的人工管理操作；其具体管理 surface 属于 T042，
不得绕过既有 Cookie/权限检查，也不得由 Agent 创建、释放或延长 hold。每次成功或失败均使用 ADR 0017 的
封闭 metadata-only 审计接口，分别使用固定 action：`RETENTION_HOLD_CREATED`、
`RETENTION_HOLD_RELEASED`、`RETENTION_HOLD_EXPIRED` 或 `RETENTION_HOLD_CHANGE_FAILED`；resource 为
`RETENTION_HOLD` 或受保护资源，且不传任意 metadata。

`BACKUP_COPY` hold 仅保护由未来 DG-08 备份设计定义的稳定副本标识；本 ADR 不假设其表结构或文件命名。
对导入/会话的 hold 不得凭猜测推导其所在备份副本，未来备份清单若需要这种关联，必须由 DG-08 明确。

### 确定性保护与删除资格

所有判定均接收一个调用开始时取得的 UTC `asOf`，并在删除事务中重新读取和锁定目标及相关 hold。清理不
能仅依据文件系统 mtime、当前配置、应用缓存、日志或任务消息判断。T042 提供机器可读的闭合结果：
`ELIGIBLE`，或以下优先级最高的第一个 protection reason：

1. `MISSING_RETENTION_FACT`：目标缺少已冻结到期/版本事实，或时间无效；
2. `ACTIVE_AUDIT_HOLD`：存在有效 `ACTIVE` hold；
3. `ROLLBACK_WINDOW`：导入批次的 `rollbackProtectedUntil` 非空且 `asOf < rollbackProtectedUntil`；
4. `ACTIVE_OPERATION`：关联 durable task、未消费且未到期的确认凭证，或其他已批准的正在执行删除不安全的
   操作仍活跃；
5. `RETENTION_NOT_DUE`：`asOf <` 已冻结到期时刻；
6. `ELIGIBLE`：以上均不成立。

边界统一为 `[createdAt, expiresAt)`：恰好 `asOf == expiresAt` 或
`asOf == rollbackProtectedUntil` 时，时间窗口已到期；但必须仍通过全部更高优先级保护检查。导入原文件以
`sourceExpiresAt` 判定，会话以 `AgentConversation.expiresAt` 判定。物理删除成功后，T031 必须在同一
业务事务写固定 `RETENTION_ARTIFACT_DELETED` 审计；被保护而跳过时可批量写固定
`RETENTION_CLEANUP_SKIPPED_PROTECTED`，不得携带内容或自由文本。任何存储缺失、I/O 失败或并发状态变化
都不是“已删除”，必须保持/恢复可重试事实并写固定失败审计。

备份轮转只在副本已超出 ADR 0009 对应日/周/月集合、未被 `BACKUP_COPY` 的有效 hold 保护，且没有未完成的
恢复演练引用时才是 `ELIGIBLE`。受保护副本绝不自动删除，也不因 hold 而重分类、覆盖或重置其原有轮转
年龄；hold 释放/过期后，下一次轮转按原始创建时间重新判定。T036 必须在删除前以同一 `asOf` 复核该
predicate 并记录固定 `BACKUP_COPY_DELETED`/失败审计，但其加密、清单和一致性实现仍等待 DG-08。

### 配置、审计与执行边界

系统配置 release 是策略值的唯一权威来源；已冻结到期时间、`retentionConfigVersion` 和 hold 行是每个
具体对象的执行权威来源。配置发布只影响之后创建的对象；它不会缩短或延长旧对象、自动释放 hold、或改变
已经生成的 backup copy。清理和轮转读取配置仅用于展示/新建决策，绝不替代对象事实。

配置发布沿用 `SYSTEM_CONFIG_PUBLISHED` 审计，资源为其 release；留存对象/hold/删除事件各自使用上述
固定 action 和 resource id。审计链、导入批次业务历史、确认产生的风险/待办/时间线以及对话之外已确认的
业务记录均不属于本 ADR 的删除目标，继续保留。Celery 任务只传稳定资源 ID、`asOf` 和配置版本引用，
不得传文件/对话/备份内容；任务重试必须重新在 PostgreSQL 执行该 predicate。

### Addendum：hold 管理 API、生命周期与并发（2026-08-12）

本 addendum 是 ADR 0027 的组成部分，并冻结 T042 所需的人工管理 surface、失败语义和 PostgreSQL
并发规则。它不定义备份副本的加密、清单或一致性机制；`BACKUP_COPY` 的实际资源解析仍受 DG-08 约束。

#### HTTP surface、权限与数据形状

新增且仅新增以下管理端点。它们使用既有 Cookie session、CSRF/request tracing 和统一 JSON envelope：成功
响应为 `{code, message, data, traceId}`，其中 `code` 为 `OK`；失败响应同形且 `data` 为 `null`。所有时间为
UTC RFC 3339 毫秒，所有 hold ID、导入批次 ID 和会话 ID 均为 UUID。请求 DTO 禁止 unknown field。

| Endpoint | Request / query | 成功结果 |
|---|---|---|
| `POST /api/admin/retention-holds` | `{resourceType, resourceId, reason, expiresAt}`；`resourceType` 为 `IMPORT_BATCH`、`AGENT_CONVERSATION` 或 `BACKUP_COPY`，`reason` 为 ADR 中的封闭枚举，`expiresAt` 为 nullable UTC 时间且非空时必须严格晚于本事务 `asOf`。`IMPORT_BATCH`/`AGENT_CONVERSATION` 的 `resourceId` 为 UUID；`BACKUP_COPY` 为 1–128 位受约束 identifier。 | 新建时 `201`，返回一个 hold；同一有效 hold 的语义相同重试时 `200`，返回原 hold。 |
| `GET /api/admin/retention-holds` | 可选 `resourceType`、`resourceId`、`status`，以及 `page`（`>=1`，默认 `1`）和 `pageSize`（`1..100`，默认 `30`）。结果按 `createdAt DESC, id DESC` 排序。 | `200`，`data` 为 `{items, total, page, pageSize}`。 |
| `GET /api/admin/retention-holds/{holdId}` | `holdId` 为 UUID。 | `200`，返回一个 hold。 |
| `POST /api/admin/retention-holds/{holdId}/release` | 空 object `{}`；不得提交 reason、expiry、resource 或任意业务字段。 | 首次有效 release 为 `200`，返回 `RELEASED` hold；对同一已 `RELEASED` hold 的重试同样为 `200`，不再产生审计事件。 |

hold 响应只含 `id`、`resourceType`、`resourceId`、`reason`、`status`、`createdAt`、`createdById`、
`expiresAt`、`releasedAt`、`releasedById`、`expiredAt`、`expiredById`。它不返回 trace ID、文件名、文件内容、
对话正文、自由文本原因、备份内容或任意 metadata。列表/详情是管理读取，不创建 hold-change 审计事件。

四个端点都要求 `admin.config.manage`；不新增 permission code，不按项目范围降级，也不授予 Agent 工具。缺少
权限返回 `403 FORBIDDEN`，未认证沿用既有 `401 UNAUTHORIZED`，请求/ID/query 校验失败返回
`422 VALIDATION_ERROR`。`IMPORT_BATCH` 或 `AGENT_CONVERSATION` 在创建时不存在，分别返回
`404 IMPORT_BATCH_NOT_FOUND` 或 `404 AGENT_CONVERSATION_NOT_FOUND`。hold ID 不存在返回
`404 RETENTION_HOLD_NOT_FOUND`。

DG-08 未解决时，T042 不得猜测、读取或创建 `BACKUP_COPY` 的资源事实：对它的 create 和 release 返回
`409 RETENTION_BACKUP_COPY_UNAVAILABLE`。表中的枚举和未来受认可的稳定 identifier 仅保留给 DG-08 批准后的
备份实现；本条不构成备份管理 contract。

#### 生命周期、幂等与冲突

创建时对同一 `(resourceType, resourceId)` 先处理到期的有效 hold，再判断唯一有效 hold。若剩余 `ACTIVE`
hold 的 `reason` 与 `expiresAt` 均与请求完全相同，则请求是幂等重试，返回既有 hold（`200`）且不另写审计；
任何一个字段不同则返回 `409 RETENTION_HOLD_ALREADY_ACTIVE`。若没有有效 hold，创建一个新的 `ACTIVE` 行并
返回 `201`。终态历史不能成为创建冲突；再次保全必须创建新的行，拥有新的 `id`、创建 actor 和时间。

唯一允许的状态转换是 `ACTIVE -> RELEASED` 与 `ACTIVE -> EXPIRED`。`RELEASED` 和 `EXPIRED` 均为不可重新
激活的终态：没有 update/extend/reopen endpoint，且需要新的 hold 而不是改写、清空或重用终态行。`expiresAt`
与创建事实在创建后不可修改。release 仅允许人工操作；自动到期使用系统 actor。在 `asOf >= expiresAt` 时，
任一保护/清理判定或 create/release 事务必须先将该行转换为 `EXPIRED` 并写 `RETENTION_HOLD_EXPIRED` 审计；
随后 release 请求返回 `409 RETENTION_HOLD_EXPIRED`，不得伪报 release 成功。对已 `EXPIRED` 的 release 也返回
该错误。除已 `RELEASED` 的上述重试外，重复 release 不产生成功事件。

PostgreSQL 必须以 partial unique index 保证每个资源最多一个 `status = 'ACTIVE'` 行，并以数据库 trigger
（而非仅 service 检查）强制以下规则：禁止 `DELETE`；`createdAt`、`createdById`、`createdTraceId`、
`resourceType`、`resourceId`、`reason`、`expiresAt` 永远不可变；仅允许上述两条从 `ACTIVE` 出发的转换；终态行
不得再更新；`RELEASED` 必须且只能具有 `releasedAt`、`releasedById`、`releasedTraceId`，`EXPIRED` 必须且只能
具有 `expiredAt`、`expiredTraceId`（`expiredById` 为 nullable system actor reference）。该 trigger 是 T042
migration 的 required persistence invariant，数据库直写也不得绕过它。

#### PostgreSQL lock ordering

所有改变 hold 或依据 hold 决定删除资格的事务都以同一个调用开始时取得的 UTC `asOf` 执行，并使用下列严格
顺序；不得先锁 hold 行再取得资源锁。

1. 对目标资源按 `(resourceType ordinal, resourceId UTF-8 byte order)` 排序，其中
   `IMPORT_BATCH = 1`、`AGENT_CONVERSATION = 2`、`BACKUP_COPY = 3`；T042 的单资源 API 只有一个键。
2. 对每个键按序取得 `pg_advisory_xact_lock(hashtextextended('retention:' || resourceType || ':' || resourceId, 0))`。
   hash collision 只能额外串行化，不能降低正确性。
3. 对 `IMPORT_BATCH` 或 `AGENT_CONVERSATION`，取得其事实行的 `SELECT ... FOR UPDATE`；create 在该行不存在时
   失败关闭并返回对应 `404`。`BACKUP_COPY` 在 DG-08 前不进入该路径。
4. 再以 `(createdAt, id)` 顺序 `SELECT ... FOR UPDATE` 该资源的 `ACTIVE` hold，及本次操作指定的 hold 行；
   到期转换、唯一性检查、create/release、保护 predicate 复核和同事务审计随后完成。

按 hold ID release 时，允许先做无锁、只读的资源键定位；行从不删除。随后必须从第 2 步重新取得资源 advisory
lock，并在第 4 步重新锁定和读取该 hold，不能依赖定位读的状态。清理任务、Celery retry 和自动过期路径必须
复用此顺序；它们不得仅锁业务资源或仅依赖 partial index。任何获取不到事实、hold、锁或一致 UTC 时间的情况
均 fail-closed，不能报告 `ELIGIBLE` 或执行删除。

#### metadata-only audit

每个已认证且已进入 hold 授权/持久化边界的 create/release 结果，都使用 ADR 0017 的 `AuditService` 写入固定
metadata-only 事件：成功为 `RETENTION_HOLD_CREATED`、`RETENTION_HOLD_RELEASED` 或
`RETENTION_HOLD_EXPIRED`；拒绝/失败为 `RETENTION_HOLD_CHANGE_FAILED`，`failureCode` 只能是对应的固定 API
machine code。module 固定 `RETENTION`，成功 resource 固定 `RETENTION_HOLD`/hold UUID；失败若 hold UUID 已知则
使用它，否则 resource 为请求的受保护资源且仅使用其受约束 identifier。审计与成功状态转换在同一事务；失败
审计在回滚业务变化后以单独事务保留。未认证请求仅走既有认证边界，不伪造 actor 审计。所有这些事件不得包含
request body、expiry 以外的内容、文件名、对话内容、自由文本、备份内容或 JSON metadata。

## Consequences

- T042 可以实现有边界的配置、冻结的留存事实、hold 持久化和 fail-closed protection service。
- T031 可以在不猜测配置或竞争状态的前提下执行可审计清理；T036 只获得保护副本删除语义，仍受 DG-08 阻塞。
- T042 恢复为 `READY`；本 ADR 本身不实现 T042、T031、T036 或任何 migration、API、任务和测试。
