# 定义周报聚合生命周期契约

状态：已批准

## Context

DG-09 未确定新增周报聚合表如何从邮件与已发布业务状态保持一致。本 ADR 兑现 ADR 0015 的真实周报聚合表要求，并保持设计 §6 的 Shanghai week 与延迟同步规则。

## Decision

### 权威源与归属

- 周归属只由已处理邮件的 `sent_at`（缺失时 `received_at`）按 `Asia/Shanghai` 换算的周一 `week_start` 决定；同步或候选发布时间不得改变归属周。
- 聚合的权威源是：已完成解析并匹配项目的邮件最小化摘要、由该邮件候选发布形成的当前 risk、该 risk 的当前关联 todo，以及风险 timeline 的确认/解除事实。未发布、忽略、解析失败或无项目匹配的候选不进入聚合。
- 聚合不是邮件正文或模型原始输出的副本；只保存项目、邮件/候选/risk/todo 的 UUID reference、计数、风险等级分布、最小化摘要、最新业务状态和 `source_revision`。

### 物化表与失效

- T004 新增 `weekly_report_aggregates`，主键为 UUID，且 `UNIQUE(week_start, project_id)`；字段为 `week_start`、`project_id`、`summary`、`risk_count`、`risk_level_counts`、`source_revision`、`stale`、`generated_at`、`freshness_deadline`、`created_at`、`updated_at`。
- 新增 `weekly_report_items`，包含 `aggregate_id`、`source_mail_id`、`source_candidate_id`、`risk_id`、`todo_id`、`source_revision`、`summary`、`risk_level`、`risk_status`、`todo_status`、`occurred_at`；`UNIQUE(aggregate_id, source_mail_id, source_candidate_id, risk_id)`。所有 source FK 使用 `RESTRICT`，以免清理破坏物化事实；内容清理由 ADR 0012/DG-04 后续策略决定。
- 邮件解析完成、候选发布/编辑、risk 创建/更新/解除/重新打开、关联 todo 更新以及项目匹配修正，均在同一业务事务内将受影响 `(week_start, project_id)` aggregate 设为 `stale=true`，并用 ADR 0018 durable task enqueue 一个 `WEEKLY_REPORT_REBUILD`。同一 `(week_start, project_id, source_revision)` 的 rebuild 以稳定 idempotency key 去重。

### Rebuild、reconciliation 与新鲜度

- rebuild 在单一 PostgreSQL transaction 内从权威源完整重算一个 `(week_start, project_id)`：删除该 aggregate 的旧 items，写入新 items 和 summary，递增 `source_revision`，设 `stale=false`、`generated_at=now()`、`freshness_deadline=generated_at + interval '15 minutes'`。失败回滚旧物化行，保留 `stale=true` 并按 ADR 0018 重试。
- 每 15 分钟 reconciliation 扫描当前周及过去 13 周的 `stale=true` 或 `freshness_deadline < now()` 行，幂等 enqueue rebuild；它也检查权威源 revision 与 aggregate revision 不一致并标记 stale。不得把 Redis、内存缓存或前端状态当作 reconciliation 事实。
- API 读取允许返回最近一次完整物化结果，并携带 `stale=true`；若不存在物化结果或已 stale 超过 15 分钟，返回 `503 WEEKLY_REPORT_STALE`，data 含 `{weekStart, projectId, retryAfterSeconds: 60}`，不得伪造实时汇总。成功读取的 `generatedAt` 不得超过 15 分钟前，除非明确标记 stale。
- 迟到邮件、候选编辑或项目匹配修正必须重建其归属周，即使该周已结束；不会移动到当前周。候选从未发布改为发布、或已发布后编辑/撤销时，均失效该邮件归属周的项目 aggregate。

### Addendum：不可变邮件接收时间契约（2026-08-12）

本 addendum 是 ADR 0021 的组成部分，解决 T027 实施时发现的 `received_at` 权威来源、持久化与重试稳定性缺口。
它只增加非内容型 envelope 时间事实，不允许持久化邮件正文、附件、完整 header 或 Provider 原始响应，也不改变
ADR 0022 的 UID/UIDVALIDITY handoff、cursor 或阶段终态契约。

#### 权威来源与规范化

- `sent_at` 来自邮件 RFC 5322 `Date` header。只有字段语法有效、日历时间有效且时区可确定时才接受；缺失、
  无效、使用 `-0000` 表示未知本地时区，或解析后没有时区的值均规范化为 `NULL`。不得把服务器接收时间、同步
  时间或处理时间伪装成 `sent_at`。
- `received_at` 的首选权威来源是 IMAP 对同一
  `(mailbox_config_id, uid_validity, imap_uid)` 返回的 `INTERNALDATE`，不是可伪造的 `Received` header、文件
  mtime、Celery 投递时间、`MailMessage.createdAt` 或 `processedAt`。T024 discovery 必须在抓取 envelope 时一并
  请求 `INTERNALDATE`。
- IMAP `INTERNALDATE` 必须符合 IMAP `date-time`，具有有效日历时间及显式数值 UTC offset；缺失、非法或无法
  唯一换算为 instant 时，使用创建首条 durable handoff 的同一 PostgreSQL transaction timestamp，来源记为
  `FIRST_DURABLE_OBSERVATION`。正常来源记为 `IMAP_INTERNALDATE`。这个降级只表示 Provider 接收事实不可用，
  不得改读邮件 header 或后续 refetch 时间。
- 所有接受的时间先按其 offset 换算为 UTC instant，再以 PostgreSQL `timestamptz(3)` 持久化；精度超过毫秒时
  截断至毫秒，不按数据库 session timezone 改变 instant。API 继续按 ADR 0019 输出 UTC RFC 3339 毫秒；仅在
  计算 `week_start` 时换算为 `Asia/Shanghai`。

#### 持久化、不可变性与既有数据

- 下一条串行 Alembic revision 为 `mail_source_handoffs` 增加非空 `receivedAt`、非空封闭来源
  `receivedAtSource`（`IMAP_INTERNALDATE`、`FIRST_DURABLE_OBSERVATION`），并增加可空 `sentAt`；为
  `mail_messages` 增加同义的非空 `receivedAt` 与 `receivedAtSource`。这些都是 envelope metadata，不是邮件
  内容。
- T024 在首次 handoff transaction 中一次写入 handoff 的 `sentAt`、`receivedAt` 与来源。若 source identity
  已存在，existing row 胜出，重复 discovery 不更新这些字段。T025 首次建立 `MailMessage` 时从该 handoff
  复制三个事实；既有 `MailMessage` 行不得在 parse retry、AI retry、manual retry 或 refetch 时重写它们。
- migration 对既有 handoff 使用其已持久化 `createdAt` 作为 `receivedAt`，来源为
  `FIRST_DURABLE_OBSERVATION`；既有 message 优先从同一 `(mailboxConfigId, batchId, imapUid)` handoff 复制，
  无匹配 handoff 时使用 message 自身既有 `createdAt` 并记同一来源。该回填在单一 PostgreSQL migration 中
  完成，随后字段改为 `NOT NULL`；不得用 `processedAt` 或 migration 执行时间回填。
- migration 必须以 PostgreSQL trigger 拒绝对上述 handoff 三个时间事实以及 message 的 `sentAt`、
  `receivedAt`、`receivedAtSource` 的后续更新。首次写入后它们是按 source identity 冻结的事实；若源端随后
  返回不同 `Date`/`INTERNALDATE`，只保留原值，不移动周归属。UIDVALIDITY 改变仍按 ADR 0022 建立新的 source
  identity，不能改写旧 identity。

#### 周归属与任务边界

- T027 对每封进入聚合的邮件只计算
  `occurred_at = COALESCE(mail_messages.sentAt, mail_messages.receivedAt)`；因为 `receivedAt` 非空，不能再回退到
  `createdAt`、`updatedAt`、`processedAt`、候选创建/发布时间或当前时间。其上海周归属一经 source identity
  冻结，retry、refetch、延迟 parse/AI/review 和候选发布均保持稳定。
- 若 migration 后仍无法取得非空、有效 UTC `receivedAt` 或来源，属于 schema/integrity failure，聚合必须
  fail closed、保持 `stale=true` 并走 ADR 0021/0018 retry，不得猜测周归属。
- 为解除当前阻塞，T027 明确拥有上述 additive metadata revision、T024 envelope/handoff 接线、T025 message
  copy 接线及相应 PostgreSQL tests；这是一项受限兼容性扩展，不重开 T024/T025 的业务语义或 checkpoint。
  邮件正文/附件存储、同步 cursor、解析、AI、候选发布及公开 mailbox API 仍不属于 T027。

## Consequences

- T004 可以唯一确定 weekly aggregate/item tables、外键、唯一约束与必要索引。
- T027 实现物化重建、查询和 freshness 行为；T028 仅通过 T027 已授权查询服务读取结果。
- T027 同时实现本 addendum 明确授权的最小 envelope 时间事实 migration 与 ingestion 接线，以便其周归属具有
  可执行的不可变 source contract。
- DG-09 解决；本 ADR 不定义邮件抓取到解析的 transient handoff（DG-10）或 retention protection（DG-04）。
