# T004 Agent 与周报能力 Schema 说明

## Agent

`agent_conversations` 由 owner 用户以 `RESTRICT` 引用；会话删除级联清理消息、事件和未保留的确认凭证。
`agent_messages` 与 `agent_events` 分别以 `(conversationId, sequence)` 唯一。迁移中的 PostgreSQL
trigger 在插入时锁定并递增会话计数器，因此 sequence 必须从 1 连续递增，且 PostgreSQL 是唯一
事件顺序事实来源。`agent_events.taskId` 以 `RESTRICT` 引用 T041 的 `durable_tasks`。

确认凭证只持久化 token、内容和授权范围的 SHA-256 digest；`canonicalContent` 保存 UTF-8 canonical
JSON，明文 token 不入库。token digest 与 idempotency key 各自唯一；结果 resource type/id 必须同时为空或同时存在。

## 周报

`weekly_report_aggregates` 按 `(weekStart, projectId)` 唯一，`weekStart` 限制为 ISO 周一；聚合维持
风险总数、等级 JSONB 分布、source revision 和 15 分钟 freshness 时间窗。`weekly_report_items` 以
`(aggregateId, sourceMailId, sourceCandidateId, riskId)` 唯一，并对 aggregate、邮件、候选、风险、待办
全部使用 `RESTRICT` 外键，留存清理不得破坏已物化事实。

T004 只提供持久化约束；重建、失效、SSE、确认消费和 retention execution 由后续任务实现。
