# 定义 Agent 执行与领域命令契约

状态：已批准

## Context

DG-06 未协调“AI 调用由 Celery Worker 执行”与实时 SSE；DG-07 未定义 Agent 的上报、处理、解除业务语义。ADR 0013 已禁止绕过领域规则，本 ADR 在不引入新任务系统或数据库的前提下确定执行和写入边界。

## Decision

### 执行、事件与流控制

- Agent AI invocation 一律由 Celery Worker 执行，使用 ADR 0018 的 `durable_tasks` 与 `task_outbox`。API request 不直接调用 Provider；task payload 仅含 `conversation_id`、`user_message_id`、`requested_by_user_id` 和 execution configuration identifier。
- Worker 对每个 conversation 在同一事务内锁定会话、递增 event/message sequence 并写入 ADR 0019 的 `agent_events`；事件先持久化再由 SSE API 读取。Redis 仅传递 task message，不能用作顺序、resume 或业务事实来源。
- 同一 conversation 同时仅允许一个非终态 Agent execution task。创建时以 `conversation_id` 为 idempotency boundary；重复请求返回同一 active task/stream，不创建第二个 Provider invocation。
- API 断线、用户取消、Worker lease 失效和 Provider timeout 都通过 durable task 状态与持久 event 处理。Worker 每 15 秒更新 durable-task lease heartbeat 并写 SSE heartbeat event；超过 90 秒未收到 Provider progress 视为 timeout，写 `error` event 后依 ADR 0018 retry policy 重试。取消在下一安全 Provider chunk 边界生效，禁止中途提交 preview 或领域写入。
- 背压采用 ADR 0019 的 per-conversation 事件上限；达到上限即终止执行并保留已写 event。reconciliation 只能恢复未完成任务，不能重排、覆盖或删除既有 sequence。

### Agent domain commands

所有 preview 都由受限工具产出 canonical content；只有 ADR 0019 token confirm 可提交。每次 confirm 重新进行 permission/project-scope 检查，并在一个事务中调用既有 risks/todos/timeline/audit 服务。

| Operation | 必填 canonical 字段 | 领域效果 | 幂等和状态规则 |
|---|---|---|---|
| `REPORT`（上报） | `projectId`, `title`, `description`, `riskLevel` | 创建一个 `ACTIVE` risk；创建一个关联 manager todo；追加 risk timeline；追加成功 audit。 | token 的 `idempotency_key` 是唯一写入键；相同 key 返回原 risk/todo。不得以文本相似度合并或覆盖既有 risk。 |
| `PROCESS`（处理） | `projectId`, `riskId`, `todoId`, `description`；可选 `dueDate`, `assigneeUserId` | 仅更新该 risk 的关联 todo 责任人/截止日/处理说明，并追加 risk timeline 与 audit。 | risk 必须为 `ACTIVE`，todo 必须属于该 risk/project；不改变 risk lifecycle，重复 key 返回原结果。 |
| `RESOLVE`（解除） | `projectId`, `riskId`, `description` | 调用既有 risk resolve 服务，将 `ACTIVE` 转为 `RESOLVED`；关闭或完成关联开放 todo；追加 timeline 与 audit。 | 仅允许 `ACTIVE -> RESOLVED`；已 `RESOLVED` 的同 key 返回原结果，其他 key 返回 `409 AGENT_RISK_ALREADY_RESOLVED`。 |

- `REPORT` 不允许无项目、无标题、无描述或无风险等级；`PROCESS` 不允许创建 todo 或改写 risk 核心字段；`RESOLVE` 不增加新 risk state。所有关联必须同 project，越权与跨项目资源按既有无存在性泄漏规则处理。
- 成功 audit action 固定为 `AGENT_REPORT_CONFIRMED`、`AGENT_PROCESS_CONFIRMED`、`AGENT_RESOLVE_CONFIRMED`，resource 为主 risk，trace/request 与 confirmation token id 以允许的 typed metadata reference 关联；失败 confirm 记录相应 failure code，但不得记录 message、preview 内容、prompt 或模型输出。

## Consequences

- T029 只负责异步编排与 preview，绝不写 risk/todo/timeline。
- T030 只经现有领域服务执行上述命令；不得引入新 risk state、任意 SQL 或独立 Agent 写模型。
- T004 需保存与本 ADR 对应的 task/event/confirmation references，但不实现 Worker 或命令服务。
- DG-06 与 DG-07 解决；本 ADR 不决定邮件 transient handoff、retention protection 或容量阈值。

## 后续批准补充

ADR 0029 将 `REPORT` 的服务端映射 `categoryId` 与 `categoryBindingDigest` 加入必填 canonical 字段，
并规定 preview/confirmation 的 active-category revalidation。`REPORT` 的其他领域效果与幂等规则不变。
