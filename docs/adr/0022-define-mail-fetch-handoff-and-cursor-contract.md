# 定义邮件抓取交接与 UID cursor 契约

状态：已批准

## Context

ADR 0006、ADR 0007 和 ADR 0018 已分别确定 Celery/Redis 任务边界、邮件内容最小留存和 PostgreSQL durable task/outbox 基础，但没有定义邮件抓取、解析和 AI 阶段之间的交接事实，也没有定义跨阶段失败与 UID cursor 的一致性语义。

本 ADR 解决 DG-10。它只定义 T024、T025、T026 之间的邮件 source handoff、阶段状态、cursor 推进和崩溃恢复契约；不修改 ADR 0018 的通用 durable-task contract，也不扩大 ADR 0007 的最小留存职责。

## Decision

### 阶段职责

- T024 负责 IMAP discovery、fetch、去重、durable handoff、batch 编排和 cursor 判定。
- T025 负责 MIME/body 清洗、受限附件解析和项目匹配。
- T026 负责 AI extraction、review 和 publication。
- fetch、durable handoff、parse、AI/review 是彼此可观察且不可混为一个“成功”的阶段。

### Raw source 保护

- 邮件正文和附件不得写入 PostgreSQL、Redis、Celery payload、日志或 audit。
- raw source 只允许存在于 Worker 内存或受控、短生命周期的临时文件中。
- 临时文件必须在成功、失败、取消、超时和 Worker 正常清理路径中删除；恢复依赖 IMAP 重抓，不依赖临时文件永久存在。
- audit 只记录 metadata-only 的阶段和失败事实，不记录正文、附件、prompt 或模型原始响应。

### Durable handoff

handoff 必须在 PostgreSQL transaction 中持久化必要的、非内容型 source reference 和脱敏元数据，至少包括：

- `mailbox_config_id`；
- `uid_validity`；
- `imap_uid`；
- 稳定的消息去重标识（如可用的 envelope `message_id`）；
- fetch、handoff、parse、AI/review 阶段状态及结构化失败代码；
- T024 batch 关联和必要的脱敏 envelope 元数据。

handoff transaction 必须与下游 durable task 和首条 transactional outbox 事实一起提交。下游 task payload 只能携带小型标识符和执行配置；T025/T026 通过 `(mailbox_config_id, uid_validity, imap_uid)` 重新连接 IMAP 获取 source。

邮件 source reference 不得依赖 Redis、Celery message 或 Worker 本地文件作为唯一事实来源。不得建立第二套任务状态表；统一复用 ADR 0018 的 `durable_tasks`、`task_outbox`、lease、fencing、retry 和 reconciliation。

### 阶段终态

每个 UID 的每个阶段必须区分以下结果：

- `RETRYABLE_FAILURE`：可通过 durable retry 或重新投递恢复；不得作为成功处理，不得推进 cursor；
- `SUCCEEDED`：该阶段已完成并满足下游 handoff 条件；
- `PERMANENT_FAILURE`：已明确不可恢复，例如 source 已删除、UID 已失效或 UIDVALIDITY 已改变；必须保留结构化失败代码和摘要，不得伪造成成功。

下游阶段失败不计为 T024 的 IMAP fetch implementation failure，但可以参与 batch 的整体 terminal/cursor 判定。永久失败是 terminal state，不得让 mailbox cursor 因单封邮件永久冻结。

### Batch 统计与 cursor

T024 batch 至少分别统计：

- `discovered`：发现的 UID 数量；
- `handed_off`：已完成 source reference、阶段事实、durable task 和 outbox transaction 的数量；
- `downstream_pending`：仍有 parse 或 AI/review 阶段未完成的数量；
- `succeeded`：所有约定阶段成功的数量；
- `retryable_failed`：仍可重试的数量；
- `permanently_failed`：已达到不可恢复终态的数量。

只有当前 batch 中每个 UID 都达到 `SUCCEEDED` 或 `PERMANENT_FAILURE` terminal state 时，batch 才可推进 cursor。存在 `RETRYABLE_FAILURE` 或未完成阶段时不得推进 cursor。

已达到 `SUCCEEDED` 或 `PERMANENT_FAILURE` 的 UID，必须依据 `(mailbox_config_id, uid_validity, imap_uid)` 去重，使 cursor 未推进期间的重复 discovery 不会重复创建业务处理事实。

cursor 推进不是下游成功的替代记录：阶段事实和结构化失败结果必须先持久化，cursor 才能推进。batch 的整体状态和 cursor 判定必须能够表达“包含永久失败但已完成”的 terminal 结果。

### IMAP identity 与 reset

- IMAP UID 只能在对应 `UIDVALIDITY` 下解释。
- 每次同步必须验证当前 folder 的 UIDVALIDITY，并将其与持久化 identity 比较。
- UIDVALIDITY 变化后，旧 cursor 和旧 UID identity 不得继续使用；系统必须进入明确的 reset/rebaseline 流程，清空或隔离旧 cursor 语义后建立新的同步基线。
- 不得静默沿用旧 cursor，也不得把 UIDVALIDITY 变化报告为普通成功。
- 原邮件已删除、UID 已失效或 source 无法按已持久化 identity 重抓时，产生结构化 `PERMANENT_FAILURE`，不伪造 fetch 或 parse 成功。

### Crash、重复投递与恢复

- fetch 前 crash：没有 handoff 事实，reconciliation 或下一次同步按 cursor/discovery 重新发现。
- fetch 后、handoff transaction 前 crash：raw source 丢弃；没有 handoff 事实，按 UID 重抓。
- handoff transaction 后、Celery dispatch 前 crash：PostgreSQL outbox 保留事实，publisher/reconciliation 重新投递。
- parse worker crash：lease expiry/fencing/reconciliation 将 durable task 置于 retry 或失败终态；下次执行按 UID 重抓 source。
- duplicate delivery：依靠 `(kind, idempotency_key)`、source identity、阶段状态和 lease fencing 吸收或拒绝重复处理。
- downstream retry：只重试对应阶段；不得将 raw source 放入消息或改变已完成阶段的成功事实。

PostgreSQL durable task/outbox 是恢复与事实来源；Redis 丢失、Celery 重复或本地临时文件丢失都不得造成不可解释的业务事实。

## Consequences

- 邮件原文不会扩散到数据库、备份、消息代理、日志或审计链。
- T025/T026 需要按 source identity 重新连接 IMAP，原邮件被删除时只能保留结构化永久失败。
- batch 统计和 cursor 判定需要读取跨阶段持久化事实；这不是 T024 私有的单阶段成功计数。
- UIDVALIDITY reset 会触发显式 rebaseline，可能重新发现已有邮件；source identity 去重防止重复业务处理。
- 本 ADR 不新增任务基础设施，不改变 ADR 0018 的状态机、outbox、lease、fencing 或 reconciliation 通用规则。
