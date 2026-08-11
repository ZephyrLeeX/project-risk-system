# 将审计简化为固定类型的 metadata-only 事件

日期：2026-08-11

状态：已批准

## Context

ADR 0008 原方案要求每个审计事件携带经过脱敏的 before/after snapshot，ADR 0014 同时要求审计
snapshot 不得包含完整密钥。T006 的实施与多轮独立 Review 证明：只要 Audit 写入接口接受任意业务
JSON，就无法仅依赖 sanitizer、keyword/path classifier 或 secret classifier 同时可靠保证敏感内容不泄漏
且合法 metadata 不丢失。相关失败、Recovery 与 remediation 证据保留在 T006 和 Wave 4 历史报告中。

2026-08-11 的人工决策取消 Audit snapshot 和 redaction system。审计改为只记录固定、明确、类型
受控的 metadata，不接受任意业务 payload。

## Decision

- Audit 事件只允许固定字段：`id`、`createdAt`、`actorUserId`、`actorType`、`module`、
  `action`、`resourceType`、`resourceId`、`result`、`traceId`、`requestId`、`projectId`、
  `failureCode`、`previousHash`、`integrityHash`。
- `actorType` 和 `result` 使用数据库 enum；ID 使用 UUID 或受约束 identifier；module/action/resource/
  failure code 使用有长度与字符集约束的 code，而不是自由文本。
- Audit write interface 必须显式声明上述业务输入字段，拒绝 unknown fields；不得出现
  `snapshot`、`metadata`、`payload`、request/response body、mail/attachment content、prompt、raw
  model response 或 secret/credential 输入。
- 删除 `beforeSnapshot`、`afterSnapshot` 以及不再适合 metadata-only 模型的 `summary`、
  `isSensitive`、`clientIp`、`userAgent` 字段。不得用新的 JSONB、free-form text 或双模式替代。
- 因为 Audit API 不接受任意业务 payload，不再实现或维护 generic redaction、sanitizer、keyword/path
  classifier、secret classifier 或 event-specific snapshot policy。
- PostgreSQL append-only、hash chain、tamper detection、transaction ownership、concurrency
  serialization、rollback 与 mutation rejection 约束保持不变。
- T015 的查询/导出只能暴露这些 metadata-only 字段；为保持旧前端 `/api` shape，detail projection
  中 `beforeSnapshot` / `afterSnapshot` 固定为 `null`，`isSensitive` 固定为 `false`，
  `sensitiveCount` 固定为 `0`，summary/context 只能由 action/resource/result/trace 等允许字段派生。
  这些兼容字段不是存储模式，也不得用于恢复 snapshot、content 或 sensitivity classifier。

## Consequences

- ADR 0008 中“携带脱敏快照”和“审计导出必须脱敏”的要求由本 ADR 取代；审计导出天然只包含
  允许的 metadata。
- ADR 0014 中“审计快照不得包含完整密钥”不再通过 redaction 实现，而是通过不存在 snapshot/
  payload 写入入口来满足；日志、异常、业务表和 AI 调用记录的其他保密要求不变。
- ADR 0015 的成熟 schema 兼容原则在 Audit snapshot 字段上由本人工决策明确例外；数据库仍使用
  单一 Alembic 路径，不保留 Prisma/FastAPI 双 schema。
- 旧 ADR、T006 `BLOCKED`、Review 失败、Recovery 和 remediation 历史不得删除或改写。
