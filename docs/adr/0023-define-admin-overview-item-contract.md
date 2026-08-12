# 定义管理概览 item 契约

状态：已批准

## Context

ADR 0019 已固定 `GET /api/admin/overview` 的顶层 envelope、字段名称和分段权限，但未固定健康检查、待处理事项和最近审计动态的 item 级形状。T016 因此不能在不自行设计公开 API 的情况下实施。本 ADR 只补足该读取接口；不改变任何既有业务 API、权限码、任务基础设施或 T025 的附件策略。

## Decision

`GET /api/admin/overview` 保持 ADR 0019 的成功 envelope，`data` 为：

```json
{
  "generatedAt": "2026-08-12T01:02:03.456Z",
  "health": [],
  "attention": [],
  "recentAudit": [],
  "unavailableSections": []
}
```

所有时间均为 UTC RFC 3339 毫秒；`generatedAt` 为本次聚合完成的时间。概览是只读聚合，不为“查看概览”本身写入 audit，避免把本次读取伪造成业务动态。

### health

具有 `admin.config.manage` 且该 section 成功聚合时，`health` 为非空数组，按以下固定顺序各含一个 item：`API`、`DATABASE`、`REDIS`、`WORKER`、`AI_PROVIDER`。每个 item 为：

```json
{
  "key": "DATABASE",
  "label": "数据库",
  "status": "HEALTHY",
  "checkedAt": "2026-08-12T01:02:03.123Z",
  "summary": "数据库连接正常",
  "code": null,
  "link": null
}
```

- `key` 固定为 `API`、`DATABASE`、`REDIS`、`WORKER` 或 `AI_PROVIDER`；`label` 是对应的安全展示文本，`summary` 是不含 secret、endpoint、用户输入、邮件内容或堆栈的安全摘要。
- `status` 固定为 `HEALTHY`、`DEGRADED`、`UNAVAILABLE`。`HEALTHY` 表示该 item 的所有必要 real check 在本次请求边界内成功；`DEGRADED` 表示至少一个必要子检查成功且至少一个失败或超时；`UNAVAILABLE` 表示没有必要子检查成功，或没有已启用的 AI Provider。不得以配置存在、历史成功、模拟值或静态文案替代 real check。
- `code` 成功时为 `null`；失败时是稳定、安全的 machine code：`TIMEOUT`、`UNREACHABLE`、`NO_ACTIVE_WORKER`、`NO_ENABLED_PROVIDER` 或 `CHECK_FAILED`。实现不得把下游异常文本直接放入 `code` 或 `summary`。
- `link` 为 `null`，或 `{ "path": "/admin/api-keys", "query": {} }`。只有 `AI_PROVIDER` 可返回后者；其余健康 item 必须为 `null`。
- `API` 验证当前 API process 的依赖注入和路由服务可用；`DATABASE` 执行无业务副作用的 PostgreSQL liveness query；`REDIS` 执行 Redis `PING`；`WORKER` 执行 Celery control-plane ping，至少一个 Worker 回复才成功；`AI_PROVIDER` 对全部已启用 Provider 通过既有受限 outbound client 执行真实连接检查。AI Provider 全部成功为 `HEALTHY`，部分成功为 `DEGRADED`，全部失败或没有 enabled Provider 为 `UNAVAILABLE`。
- 各检查独立并发，单个检查（含每个 Provider）最多 2 秒；概览不得因健康检查无限等待。超时按 `TIMEOUT` 处理，其他单项失败不得使整个 HTTP 请求失败。

### attention

具有 `risk.manage_all` 时，`attention` 为数组；没有事实时为空数组。每个 item 都对应一个可重新查询的持久事实，且为：

```json
{
  "id": "IMPORT_REVIEW:550e8400-e29b-41d4-a716-446655440000",
  "kind": "IMPORT_REVIEW",
  "status": "WARNING",
  "title": "导入批次需要复核",
  "summary": "批次包含待确认的导入结果",
  "occurredAt": "2026-08-12T01:00:00.000Z",
  "link": { "path": "/admin/imports", "query": { "batchId": "550e8400-e29b-41d4-a716-446655440000" } }
}
```

- `kind` 固定为 `IMPORT_REVIEW`、`AI_PROVIDER_CONNECTION`、`AI_PROVIDER_EXPIRY`；`status` 固定为 `CRITICAL` 或 `WARNING`。`id` 必须为 `<kind>:<resource UUID>`，并在同一次响应中唯一。
- `IMPORT_REVIEW`：每个 `PREVIEWED` import batch 各一个 item；存在任一 import/supplemental/legal error row 时为 `CRITICAL`，否则为 `WARNING`；`occurredAt` 为 batch `createdAt`；link 固定为 `/admin/imports` 和唯一 `batchId`。
- `AI_PROVIDER_CONNECTION`：每个 enabled Provider 且其已持久化 `lastTestStatus` 为 `FAILED` 或 `UNTESTED` 各一个 item；`FAILED` 为 `CRITICAL`，`UNTESTED` 为 `WARNING`；`occurredAt` 为 `lastTestAt`，未测试时为 Provider `updatedAt`；link 固定为 `/admin/api-keys` 和唯一 `providerId`。
- `AI_PROVIDER_EXPIRY`：每个 enabled Provider 且 `expiresAt` 早于当前时刻加 30 天的 Provider 各一个 item；已过期为 `CRITICAL`，否则为 `WARNING`；`occurredAt` 为 `expiresAt` 在 UTC 当日 `00:00:00.000Z`；link 同 Provider connection。`expiresAt` 为 `null` 的 Provider 不产生此 item。
- `title` 和 `summary` 是服务端产生的安全展示文案，不得包含 API key、Provider endpoint、导入内容或审计快照。`link` 的 `path` 仅可为 `/admin/imports` 或 `/admin/api-keys`；`query` 只可含与该 `kind` 对应的一个 UUID 参数。前端以该 path/query 导航并由目标页重新授权、重新查询，不能将概览 item 视为授权凭证。
- 排序固定为：`CRITICAL` 在前、再按 `occurredAt` 降序、最后按 `id` 升序。不得用 synthetic、推测或不能定位到资源的 aggregate item 填充列表。

### recentAudit

具有 `admin.audit.view` 时，`recentAudit` 为最近 10 条 audit log 的数组；每个 item 为：

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "occurredAt": "2026-08-12T01:00:00.000Z",
  "actorName": "系统管理员",
  "module": "IMPORT",
  "action": "PROJECT_IMPORT_IMPORTED",
  "summary": "项目导入已完成",
  "result": "SUCCESS",
  "resourceType": "IMPORT_BATCH",
  "resourceId": "550e8400-e29b-41d4-a716-446655440001",
  "traceId": "550e8400-e29b-41d4-a716-446655440002",
  "link": { "path": "/admin/audit-logs", "query": { "auditId": "550e8400-e29b-41d4-a716-446655440000" } }
}
```

字段直接取自已批准的 metadata-only audit list：`module` 使用既有 `AuditModuleKey`，`result` 使用 `SUCCESS` 或 `FAILURE`，`summary` 使用已脱敏的 audit summary。不得返回 actor account、client IP、client、before/after snapshot、context、hash 或任何敏感值。排序固定为 `createdAt` 降序、`id` 降序；`occurredAt` 等于 `createdAt`。link 恒为 `/admin/audit-logs` 及唯一 `auditId`，目标页必须重新执行其既有权限检查。

### unavailable、partial data 与错误

`unavailableSections` 的每项为 `{ "section": "health", "reason": "FORBIDDEN", "code": "FORBIDDEN" }`；`section` 固定为 `health`、`attention` 或 `recentAudit`，`reason` 固定为 `FORBIDDEN`、`TIMEOUT` 或 `DEPENDENCY_FAILURE`，`code` 为对应的安全 machine code。该数组按 `health`、`attention`、`recentAudit` 的固定顺序排列，且同一 section 最多一项。

- 未具备 section 所需权限时，HTTP 仍为 `200`，该 section 为 `null`，并加入 `{section, reason: "FORBIDDEN", code: "FORBIDDEN"}`。这落实 ADR 0019 的分段授权，且不得改用其他 section 的权限扩大读取范围。
- 已获授权但 section 的聚合在受控边界内超时或依赖失败时，HTTP 仍为 `200`，该 section 为 `null`，并以 `TIMEOUT` 或 `DEPENDENCY_FAILURE` 说明。单个 health item 的失败是该 item 的 `UNAVAILABLE`/`DEGRADED`，不是 health section 不可用。
- 会话未认证遵循既有 `401 UNAUTHORIZED`；该 endpoint 没有 query/body 参数。除上述 section-level partial response 外，无法安全构造 envelope 的未处理服务器错误遵循统一 `500 INTERNAL_ERROR` envelope。不得把 Provider、Redis、Celery 或数据库的原始异常暴露给客户端。

## Consequences

- T016 可在独立的 admin overview module 中实现 schema、受控依赖检查、聚合、OpenAPI 和 dependency-fault-injection tests，无需新增数据库、Redis 业务事实或监控平台。
- T033 可使用稳定 item/status/link 语义替换当前页面固定健康、待办和审计数组；目标页面的资源查询与授权保持其自身边界。
- ADR 0019 的顶层字段和分段权限不变；本 ADR 是其 `/api/admin/overview` item 级补充。
