# 定义 Agent API、确认凭证和 SSE 契约

状态：已批准

## Context

DG-01 和 DG-03 未定义 Agent、周报和管理概览的公开契约，亦未定义确认凭证的内容绑定和 SSE 断线恢复语义。ADR 0013 与 ADR 0016 已确定受限工具、显式确认和 SSE 的总体边界；本 ADR 仅将这些边界固化为可实现、可测试的 API 与持久化契约。

## Decision

### 统一 HTTP 与授权

- 所有下列 JSON 成功响应使用既有 envelope：`{code, message, data, traceId}`；失败响应同形，`data` 为 `null`。时间为 UTC RFC 3339 毫秒，标识符为 UUID。
- 不新增 permission code。`agent.use` 是所有 Agent endpoint 的前提；只读查询及周报还须 `dashboard.view`；`REPORT` 预览/确认还须 `risk.report`，`PROCESS` 与 `RESOLVE` 还须 `risk.resolve`。所有查询及确认于执行时重新应用当前项目范围。
- 默认角色授权沿用 Seed：`RISK_ADMIN` 与 `PROJECT_MANAGER` 具有 `agent.use`、`risk.report`、`risk.resolve`；`VIEWER_AUDITOR` 只有 `agent.use`，不得发起 mutation preview 或 confirm；`SYSTEM_ADMIN` 不因系统管理员身份隐式取得 Agent 或风险权限。

### 公开 endpoint 与数据形状

- `POST /api/agent/conversations`：请求 `{message: string(1..4000)}`；创建会话和首条用户消息，返回 `{conversation, userMessage, streamUrl}`，状态 `201`。
- `POST /api/agent/conversations/{conversationId}/messages`：请求同上；追加用户消息并排入 Agent execution，返回 `{userMessage, streamUrl}`，状态 `202`。
- `GET /api/agent/conversations/{conversationId}` 返回 `{conversation, messages, nextMessageSequence}`；`GET /api/agent/conversations/{conversationId}/messages?afterSequence=<non-negative integer>&limit=<1..100>` 返回 `{items, nextAfterSequence}`。
- `GET /api/agent/help` 返回 `{tools}`；每项仅含 `name`、`description`、`requiredPermissions`、`supportsPreview`。工具目录不得暴露内部 prompt、SQL 或 Provider 配置。
- `GET /api/weekly-reports/current` 与 `GET /api/weekly-reports/{weekStart}` 返回 `{weekStart, weekEnd, generatedAt, stale, freshnessDeadline, summary, projects}`；`weekStart` 为 Shanghai 周一的 `YYYY-MM-DD`。详情通过 `GET /api/weekly-reports/{weekStart}/projects/{projectId}` 返回 `{weekStart, project, items, generatedAt, stale}`。具体 summary/item 字段以 ADR 0021 的物化行定义为准。
- `GET /api/admin/overview` 返回 `{generatedAt, health, attention, recentAudit, unavailableSections}`；仅分别复用既有管理权限：health 需 `admin.config.manage`，attention 需 `risk.manage_all`，recentAudit 需 `admin.audit.view`。任一部分无权限时该部分为 `null`，并在 `unavailableSections` 说明，不扩大查询范围。
- 资源不存在或不在当前范围内返回 `404 AGENT_CONVERSATION_NOT_FOUND`、`404 WEEKLY_REPORT_NOT_FOUND` 或 `404 PROJECT_NOT_FOUND`；权限不足返回 `403 FORBIDDEN`；请求校验失败返回 `422 VALIDATION_ERROR`。

### Conversation、message、event 与 confirmation persistence

- T004 必须新增 `agent_conversations`：`id`、`owner_user_id`、`created_at`、`updated_at`、`expires_at`、`last_message_sequence`、`last_event_sequence`。仅 owner 可读取；删除 owner 使用 `RESTRICT`，会话内容按 ADR 0012 清理。
- 新增 `agent_messages`：`id`、`conversation_id`、`sequence`、`role` (`USER`/`ASSISTANT`/`TOOL`)、`content`、`trace_id`、`data_as_of`、`created_at`。`UNIQUE(conversation_id, sequence)`；sequence 从 1 连续递增。
- 新增 `agent_events`：`id`、`conversation_id`、`message_id`、`task_id`、`sequence`、`type`、`payload`、`created_at`。`UNIQUE(conversation_id, sequence)`，`id` 是 SSE event ID；`task_id` 为 `durable_tasks.id` 的 `RESTRICT` 外键。PostgreSQL 是唯一有序 event fact source；Redis/Celery 不保存或恢复 event facts。
- 新增 `agent_confirmation_tokens`：`id`、`token_digest`、`owner_user_id`、`conversation_id`、`operation`、`canonical_content`、`content_digest`、`scope_digest`、`idempotency_key`、`issued_at`、`expires_at`、`used_at`、`result_resource_type`、`result_resource_id`。`token_digest` 和 `idempotency_key` 唯一；token 明文只在 issue 响应/SSE preview 中出现一次，数据库只保存 SHA-256 digest。
- canonical content 是 UTF-8、键按 Unicode code-point 升序、无空白序列化的 JSON object，字段固定为 `operation`、`projectId`、`riskId`（可空）、`todoId`（可空）、`title`、`description`、`riskLevel`（可空）、`dueDate`（可空）、`assigneeUserId`（可空）。`content_digest = SHA-256(canonical_content)`；`scope_digest` 为当前授权的 actor、permission codes、project-scope mode 与允许 project IDs 的同一 canonical JSON digest。

### Confirmation 语义

- `POST /api/agent/confirmations/{token}` 请求为空 object，成功返回 `{operation, resourceType, resourceId, completedAt}`，状态 `200`；客户端不得重新提交业务字段。
- token TTL 固定为自 `issued_at` 起 10 分钟。确认使用单个 PostgreSQL transaction，并以 `used_at IS NULL AND expires_at > now()` 条件原子消费。
- 错误固定为：`410 AGENT_CONFIRMATION_EXPIRED`（过期）、`409 AGENT_CONFIRMATION_ALREADY_USED`（已消费）、`403 AGENT_CONFIRMATION_OWNER_MISMATCH`（非签发用户）、`409 AGENT_CONFIRMATION_CONTENT_MISMATCH`（canonical content、scope 或 current permission 不匹配）、`409 AGENT_CONFIRMATION_IN_PROGRESS`（并发消费尚未完成）。已成功的同一 `idempotency_key` 返回原成功结果，不重复执行业务写入。

### SSE contract

- `GET /api/agent/conversations/{conversationId}/events?after=<event-id>` 使用 `text/event-stream`。每个事件都有 `id: <agent_events.id>`、`event: <type>` 与 JSON `data`，共同字段为 `conversationId`、`messageId`、`sequence`、`traceId`、`occurredAt`。
- 固定 event type：`message.delta` (`text`)、`progress` (`stage`, `message`)、`preview` (`operation`, `content`, `contentDigest`, `confirmationToken`, `expiresAt`)、`completed` (`dataAsOf`)、`error` (`code`, `message`, `retryable`) 与 `heartbeat`。事件 sequence 在单一 conversation 内严格递增；`preview` 永不提交业务写入。
- `after` 缺失时从当前连接建立后新增事件开始；给出 event ID 时，从其后的 sequence 补发。未知 event ID、非本会话 event ID 或已被保留策略清理的 cursor 返回 `409 AGENT_EVENT_CURSOR_UNRECOVERABLE`，响应 data 含 `restartFrom: "conversation"`；客户端必须重新读取会话/消息，不得猜测缺失事件。
- 服务每 15 秒发送 `heartbeat`；API 在 60 秒无新持久事件后发送可恢复 `error` 并关闭连接。客户端断开只登记 cancellation request；Worker 在当前安全 Provider chunk 结束后持久化 `CANCELLED` error 事件并停止。超过每会话 256 个未消费持久事件或 1 MiB payload 时，Worker 停止生成、持久化 `error` (`AGENT_STREAM_BACKPRESSURE`) 并关闭；不丢弃已有 event fact。

## Consequences

- T004 可以以此 ADR 唯一确定 Agent conversation/message/event/confirmation schema、约束和索引。
- T016、T027-T030、T034、T037、T039、T040 必须遵循此 API、错误、权限和 SSE contract。
- DG-01 与 DG-03 解决；本 ADR 不定义 Agent 领域写操作或周报物化生命周期。
