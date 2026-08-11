# T041 Durable Task 状态迁移说明

状态集合固定为 `QUEUED`、`RUNNING`、`RETRY_WAIT`、`SUCCEEDED`、`FAILED`、`CANCELLED`。

合法迁移：

- `QUEUED` → `RUNNING` / `CANCELLED`
- `RUNNING` → `SUCCEEDED` / `RETRY_WAIT` / `FAILED` / `CANCELLED`
- `RETRY_WAIT` → `QUEUED` / `FAILED` / `CANCELLED`
- `SUCCEEDED`、`FAILED`、`CANCELLED` 为终态

T041 按 ADR 0018 只在 PostgreSQL schema 中强制结构性不变量：状态和 kind registry、
`UNIQUE(kind, idempotency_key)`、attempt bounds、lease/heartbeat/expiry 字段组合、retry/completion
字段组合、outbox generation 唯一性，以及 domain batch 外键方向。数据库不使用 trigger 实现状态机。

T008 repository 必须使用包含预期旧状态的原子条件 `UPDATE`；heartbeat 及 RUNNING attempt 的完成、
失败、重试和取消还必须匹配当前 `lease_token`。零行更新表示状态或 fencing 冲突，不得退化为读后写。
task kind 与 domain batch 类型的匹配同样由后续领域 service/repository 边界校验。
