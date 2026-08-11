# 定义持久化后台任务契约

状态：已批准

## Context

ADR 0006 已确定耗时和可重试工作由 Celery Worker 执行，PostgreSQL 保存业务任务及处理状态，Redis 仅作为消息代理。但是，现有设计没有确定统一的任务状态机、幂等边界、租约与恢复语义、transactional outbox，以及领域批次与任务记录之间的所有权和引用方向。各业务模块若自行补充这些决策，会形成互不兼容的任务状态和恢复机制。

本 ADR 解决 DG-02。Agent AI execution 的进程边界、事件传输、顺序、取消和背压不在本 ADR 决定，由 DG-06 处理。

## Decision

### 持久化模型与所有权

- 可靠性基础模块统一拥有 `durable_tasks` 和 `task_outbox`。
- 初始 task kind 为 `IMPORT_PREVIEW`、`MAILBOX_SYNC`、`MAIL_MESSAGE_RETRY`、`ATTACHMENT_PARSE`、`MAIL_AI_REVIEW_PUBLISH` 和 `RETENTION_CLEANUP`。新增 kind 必须通过显式设计和 migration 加入统一 registry/数据库约束，不允许业务模块使用未登记的自由文本 kind。
- 需要领域批次的任务，由 domain batch 使用 `task_id NOT NULL UNIQUE` 外键引用 `durable_tasks.id`；外键删除行为为 `RESTRICT`。
- `durable_tasks` 不保存指向领域表的多态外键。task kind 与领域批次类型的匹配由领域 service/repository 边界校验。
- domain batch、`durable_tasks` 和首条 `task_outbox` 记录必须在同一个 PostgreSQL transaction 中创建。系统级、无需领域批次的任务可以只创建 task 和 outbox。

### 状态机

`durable_tasks.status` 只允许以下状态：

- `QUEUED`
- `RUNNING`
- `RETRY_WAIT`
- `SUCCEEDED`
- `FAILED`
- `CANCELLED`

合法迁移为：

- `QUEUED` → `RUNNING` 或 `CANCELLED`
- `RUNNING` → `SUCCEEDED`、`RETRY_WAIT`、`FAILED` 或 `CANCELLED`
- `RETRY_WAIT` → `QUEUED`、`FAILED` 或 `CANCELLED`

`SUCCEEDED`、`FAILED` 和 `CANCELLED` 是不可再次迁移的终态。

状态迁移必须通过可靠性模块 repository 的原子条件 `UPDATE` 完成，更新条件必须包含预期旧状态；涉及运行中 attempt 的 heartbeat、完成、失败或重试更新还必须包含当前 lease token。零行更新表示状态或 fencing 冲突，调用方必须明确处理，不得以读后写代替原子条件更新。数据库 constraint 负责状态值、字段组合、唯一性和外键等结构性约束；不使用数据库 trigger 实现状态机。

### 幂等性

- PostgreSQL 使用 `UNIQUE(kind, idempotency_key)` 作为任务创建的幂等边界。
- `idempotency_key` 必须是稳定、非空的业务键，并包含该 kind 所需的业务 scope/资源标识；不得依赖随机 Celery message ID。
- 相同 `(kind, idempotency_key)` 的重复提交返回已有 task，不创建第二条记录。任务进入终态后也不得复用该组合；需要一次新的业务执行时必须生成新的业务幂等键。

### Payload 与消息契约

- task payload 只允许小型、可序列化的标识符和执行配置。文件、邮件正文、附件内容、大型 workbook/JSON、其他大型业务内容以及领域结果不得存入 task payload。
- 领域结果由所属领域表保存；task 仅保存可靠性所需的状态、尝试次数、调度时间以及结构化、脱敏的失败代码和摘要。
- Redis/Celery message 只携带 `task_id` 和 `dispatch_generation`。Worker 必须从 PostgreSQL 读取当前 task 和领域输入，不得把 broker message 当作业务事实。

### Lease、heartbeat 与 fencing

- Worker 必须通过原子条件更新从 `QUEUED` 领取任务并进入 `RUNNING`，同时生成不可猜测的 lease token，记录 lease owner、heartbeat 和 lease expiry，并增加 attempt 计数。
- heartbeat 只能在 task 仍为 `RUNNING` 且 lease token 匹配时延长 expiry。
- attempt 的成功、失败、重试或取消提交必须携带并匹配当前 lease token。lease 被替换或失效后，旧 Worker 的 heartbeat 和最终提交必须失败，从而形成 fencing。
- 最大尝试次数、下一次重试时间和有限退避由 PostgreSQL task 记录维护。lease 到期不等于任务成功，也不得直接删除任务事实。

### Transactional outbox 与投递

- `task_outbox` 是 PostgreSQL transactional outbox。每次首次投递或重新投递使用递增的 `dispatch_generation`，同一 task/generation 只能有一条 outbox 记录。
- outbox publisher 在数据库 transaction 提交后向 Celery 发布。投递语义为 at-least-once；publish 与 outbox 确认之间的进程崩溃可能产生重复消息，Worker 必须依靠 task 状态、generation、幂等键和 lease fencing 安全拒绝或吸收重复投递。
- Redis 不保存不可恢复的唯一状态；broker 丢失全部消息后，系统仍必须能够仅根据 PostgreSQL 重建待投递工作。

### Reconciliation

Reconciler 以 PostgreSQL 为事实源，使用有界批次、行锁和适用时的 `SKIP LOCKED`，至少处理：

- lost dispatch：为仍需执行但缺少有效投递的 `QUEUED` task 创建新的 dispatch generation/outbox；
- retry due：将到期的 `RETRY_WAIT` task 原子迁移为 `QUEUED`，并创建新的 generation/outbox；
- expired lease：使过期 lease 失效，并根据 retry policy 和剩余 attempt 将 `RUNNING` task 转为 `RETRY_WAIT` 或 `FAILED`。

多个 reconciler 或 publisher 并发运行时，不得为同一 task/generation 创建重复 outbox，也不得覆盖有效 Worker 的 lease。

## Consequences

- T041 提供唯一的 task/outbox schema、约束和外键方向；后续业务模块不得建立第二套持久任务状态表。
- T008 必须实现本 ADR 的原子状态迁移、outbox publisher、lease/heartbeat/fencing 和 reconciliation。
- Celery 和 Redis 故障可能造成重复投递，但不会成为业务事实丢失或重复领域提交的理由。
- 领域模块保有自己的 batch、输入和结果；可靠性模块不通过多态引用依赖领域表。
- Agent AI execution 仍受 DG-06 阻塞，本 ADR 不得被解释为批准其 Worker/SSE 执行方式。
