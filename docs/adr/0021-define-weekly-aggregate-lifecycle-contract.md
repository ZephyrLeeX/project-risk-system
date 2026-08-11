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

## Consequences

- T004 可以唯一确定 weekly aggregate/item tables、外键、唯一约束与必要索引。
- T027 实现物化重建、查询和 freshness 行为；T028 仅通过 T027 已授权查询服务读取结果。
- DG-09 解决；本 ADR 不定义邮件抓取到解析的 transient handoff（DG-10）或 retention protection（DG-04）。
