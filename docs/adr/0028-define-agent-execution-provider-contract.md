# 定义 Agent durable execution 与受限 Provider 契约

状态：已批准

## Context

ADR 0019 定义了 Agent 的公开 SSE 事件和 PostgreSQL event fact，ADR 0020 要求
Agent invocation 通过 ADR 0018 durable task/outbox 执行。但它们没有登记 Agent
task kind、冻结 execution configuration 的权威事实，或不可信 Provider 输出的受限
协议。这使 T029 无法安全地创建 Worker、fake Provider 边界或 malformed-output
测试。

本 ADR 仅解决该 T029 `DESIGN_GAP`。它不实现 T029，不改变 ADR 0019 的 HTTP/SSE
公开契约，不执行任何领域写入，也不授权 T030 的 confirmation 执行。

## Decision

### Durable execution 与固定配置

- ADR 0018 的统一 `DurableTaskKind`、PostgreSQL enum 和 closed registry 新增
  `AGENT_EXECUTION`。其定义固定为 `max_attempts=3`、`timeout_seconds=90`、
  `retry_backoff_seconds=30`；第 2、3 次 retry 的 delay 分别为 30、60 秒。不得以
  Provider 管理记录的 `retryCount` 扩大此 task retry budget。
- 每条用户 Agent message 至多创建一个 execution，稳定 idempotency key 为
  `agent-execution:<conversation_id>:<user_message_id>`。在创建 transaction 中锁定
  conversation，并拒绝同一 conversation 既有 `QUEUED`、`RUNNING` 或 `RETRY_WAIT`
  的 `AGENT_EXECUTION`；重复请求返回该 active task/stream。终态后同一 user message
  不会重新排队；新的执行必须来自新的 user message。
- Agent 模块新增 PostgreSQL `agent_execution_configs`，它是 execution configuration
  identifier 的唯一权威持久化来源，不是 Redis/Celery payload。每一行具有
  `id`、`task_id UNIQUE NOT NULL REFERENCES durable_tasks(id) ON DELETE RESTRICT`、
  `conversation_id`、`user_message_id UNIQUE`、`requested_by_user_id`、
  `provider_config_id NULL REFERENCES ai_provider_configs(id) ON DELETE RESTRICT`、
  `provider_name_snapshot`、`endpoint_snapshot`、`model_snapshot`、
  `encrypted_api_key_snapshot`、`timeout_seconds`、`created_at` 和
  `cancellation_requested_at NULL`。前三个 Agent references 均为 `RESTRICT`。
- configuration 的 provider snapshot 字段在创建后不可更新；仅
  `cancellation_requested_at` 可由取消路径以 `NULL -> timestamp` 原子方式写入。
  有可用 Provider 时 `provider_config_id`、全部 snapshot 字段和 encrypted key 必须
  同时非空；无可用 Provider 时它们必须全为 null，且 task 会产生确定的失败 event。
  加密 key snapshot 只保存在 PostgreSQL encrypted-at-rest secret 字段，随会话 retention
  删除；它不得进入 task payload、Redis、SSE、audit、application log 或 error。
- 排队 API 在同一 PostgreSQL transaction 中选择当前 `enabled` 且
  `lastTestStatus=HEALTHY` 的 default Provider，生成 configuration snapshot、
  `AGENT_EXECUTION` task 和首条 outbox。task payload 恰含小型标识符
  `conversation_id`、`user_message_id`、`requested_by_user_id`、
  `execution_configuration_id`；Redis/Celery message 仍严格只含 `task_id` 与
  `dispatch_generation`。
- Worker 以 payload 中的 UUID 读取 `agent_execution_configs`，并验证它与已 claim 的
  task、conversation、user message 和 requester 完全匹配；随后只使用 immutable
  snapshot（包括 effective timeout）连接 Provider。它不得在 retry、reconcile 或
  Provider key/configuration 管理变更后重新选择 default Provider。缺少、失配或已失效
  的 configuration 是 `AGENT_EXECUTION_CONFIG_INVALID` 的非 retryable failure。
- 为保持 ADR 0018 的 broker message 不变，可靠性 executor 将已 claim 的 `task_id` 和
  lease token 作为仅进程内 handler context 传入；该 context 不写回 task payload，也不
  发送到 Celery。Agent Worker 使用它写 `agent_events.task_id` 并完成 fenced task state
  transition。

### `AGENT_PROVIDER_EXECUTION_V1` request / response

- Provider 调用只接受 OpenAI-compatible JSON completion transport；请求和响应只在
  Worker memory 中存在。每次 HTTP response body 上限 128 KiB，解析前必须为 UTF-8 JSON
  object；请求（包含 message、历史、tool catalogue 或 tool results）上限 64 KiB。超限
  一律失败，不截断或猜测业务含义。
- 每次 execution 最多两轮，正常路径固定为 `PLAN` 后 `RESPOND`。`PLAN` request 仅含协议版本、
  `phase`、当前 user message、最多 12 条最近 conversation messages（合计最多 24 KiB）
  及闭合的 read-tool catalogue；`RESPOND` request 仅额外包含 PLAN 已验证并执行的 tool
  results。至多 8 个 tool calls，全部 tool results 合计最多 48 KiB。超限的 tool result
  终止为 `AGENT_TOOL_RESULT_TOO_LARGE`，不发送给 Provider。
- 每轮 response 必须恰为
  `{ "protocol": "AGENT_PROVIDER_EXECUTION_V1", "phase": "PLAN"|"RESPOND", "actions": [...] }`
  且没有未知字段。action 总数最多 64，所有 text 总计最多 32 KiB。允许的 action 仅为：

  - `{ "type":"progress", "stage":"analyzing"|"querying"|"drafting", "message": string(1..256) }`；
  - 仅 `PLAN` 可用的 `{ "type":"tool_call", "name": <T028 closed read tool>, "arguments": <该 tool 的严格 schema> }`；
  - 仅 `RESPOND` 可用的 `{ "type":"text_delta", "text": string(1..4096) }`；
  - 仅 `RESPOND` 可用的 `{ "type":"preview_proposal", "operation":"REPORT"|"PROCESS"|"RESOLVE", "content": <ADR 0019 fixed canonical fields> }`。

- `PLAN` 不得含 `text_delta` 或 `preview_proposal`；`RESPOND` 不得含 `tool_call`。每轮最多
  一个 preview proposal；有 preview proposal 时，Worker 先以 ADR 0019 canonicalization、
  ADR 0020 operation required fields、当前 permission/project-scope 及同项目资源关系作
  本地验证，才可 issue token 并持久化 `preview` event。Provider 不能选择 token、
  `contentDigest`、`expiresAt`、event ID/sequence 或任何领域写入字段以外的字段。
- tool name 必须位于 T028 registry；arguments 由该 tool 的 `StrictRequestModel` 验证，
  每次 invoke 使用 requester 的当前 identity，重新应用权限和项目范围。Provider 没有 SQL、
  HTTP、filesystem、Celery、database 或 mutation-tool capability。Provider response 中的
  text 与 progress 只有在完整 protocol validation 后才会转为 ADR 0019 `message.delta`/
  `progress` event；原始 Provider response 永不持久化。

### Invalid output、retry、timeout 与取消

- JSON/envelope/unknown-field/phase/action/order/size/schema/canonical-content 失败，或 tool
  不在 whitelist、arguments 无效、preview 本地校验失败，统一产生终态 SSE `error`
  `{code:"AGENT_PROVIDER_INVALID_OUTPUT", message:"AI服务返回内容不符合Agent协议", retryable:false}`。
  已验证且已持久化的 earlier event facts 保留；无效 action 及 raw response 不保留。
- Provider transport timeout、DNS/connection failure、HTTP 408/429 或 5xx 产生
  `AGENT_PROVIDER_UNAVAILABLE`、`retryable:true`，并仅按本 ADR 的三次 durable retry
  执行；其他 non-2xx 为 `AGENT_PROVIDER_REQUEST_REJECTED`、`retryable:false`。无 healthy
  Provider configuration 时写 `AGENT_PROVIDER_UNAVAILABLE`、`retryable:false`，绝不返回
  mock answer。
- 每个 attempt 从 claim 起最多 90 秒。每 15 秒在同一 fenced Worker transaction 更新
  durable-task lease heartbeat 并持久化 ADR 0019 `heartbeat` event；任何 Provider round
  都只能使用截止时间的剩余时间。到达 deadline 或连续 90 秒没有 Provider progress 时按
  retryable timeout 处理。Worker 不得在一个 attempt 内超过两次 Provider round。
- 客户端断开仅对其 active execution 的 configuration 原子登记 cancellation request；它
  不删除 event、不修改 conversation，也不直接取消 task。Worker 在当前安全 Provider round
  返回或超时后、在持久化任何后续 text/preview 前读取该标记，写 `error`
  `AGENT_EXECUTION_CANCELLED`（`retryable:false`）并以 fenced `CANCELLED` 终止 task。
  reconciliation 只恢复未终态 task；已登记取消的 task 必须终止，不能 retry。lease fencing
  失败的旧 Worker 不得写 event、token 或 task terminal state。

### PostgreSQL SSE、observability 与禁止项

- 每个 validated progress/text/preview/error/heartbeat/completed event 都在同一
  PostgreSQL transaction 中锁定 conversation，递增 `lastEventSequence` 后写入
  `agent_events`；`agent_events` 是唯一 SSE resume/order fact。SSE route 只读取它，继续
  遵守 ADR 0019 的 256 events/1 MiB backpressure、15 秒 heartbeat、60 秒 idle close 与
  cursor 行为。`completed` 仅在 Worker 持久化 assistant message（若有）和所有 event
  facts 后写入，再将 task `SUCCEEDED`。
- `durable_tasks.failureCode/failureSummary`、`AiCallLog` 和 audit 只可保存固定 code、
  retryability、duration/token counts、provider ID/name/model snapshot、task/conversation/
  trace reference 等 metadata。不得保存 prompt、Provider request/response、action array、
  tool arguments/results、assistant text、preview content、API key 或任何 raw execution
  payload。Redis/Celery 永远不保存或恢复这些内容；日志不得把它们作为事实或诊断 payload。
- Provider output 绝不直接产生 risk/todo/timeline/audit business mutation。唯一允许的写
  intent 是已本地验证的 `preview` 和其 ADR 0019 confirmation token；T030 仍是该 token
  消费和领域写入的唯一授权 Task。

### Module-local Celery entrypoint 与 composition ownership

- T029 必须在 Agent 模块内暴露一个显式 dependency-injected 的 Worker entrypoint factory。
  它接收 T029 执行所需的 session factory、受限 Provider adapter 和 T028 read-tool registry，
  并返回只包含 `AGENT_EXECUTION` 的 T008-compatible handler mapping/registration descriptor。
  该 entrypoint 不得 import、修改或隐式注册到 shared production `celery_app`，不得创建全局
  session/Provider 实例，也不得合并其他模块的 handler。
- T029 可在测试 composition 中创建隔离的 Celery app，把上述 module-local entrypoint 传给
  T008 `register_executor`，并用 fake Provider 与真实 worker process 验证 task discovery、
  durable claim/fencing、Provider/SSE success、invalid output、timeout 和 cancellation。该验证
  满足 T029 的 Worker/Provider acceptance；production worker discovery 不属于 T029。
- T040 是 T002 之后 shared FastAPI/Celery composition root 的唯一 owner。T040 才能创建并
  管理 production session factory 生命周期、从 settings/secret boundary 构造 Provider
  adapter、组装 T028 registry、合并所有 module-local handler mappings，并对 shared
  `celery_app` 恰好一次调用 T008 registration。T040 还负责 production startup/readiness、
  shutdown 和所有 worker task discoverability smoke tests；不得把业务 orchestration 回收到
  composition root 或改变 T029 module contract。
- 因此 T029 无需也不得修改 T040 的 shared bootstrap/composition write-set 即可满足自身
  acceptance criteria。若 module-local entrypoint 无法仅通过显式依赖完成，T029 必须再次
  报告 `DESIGN_DEVIATION`，不能提前实施 production wiring。

## Consequences

- T029 现在可新增 `AGENT_EXECUTION` migration/registry、immutable execution configuration
  repository、Provider adapter/orchestrator、SSE persistence，以及 fake Provider 的
  success/invalid/timeout/cancellation tests 和上述 module-local Worker entrypoint。
- T040 消费该 entrypoint 完成 production registration；T029 不拥有 shared FastAPI/Celery
  composition root。
- 本 ADR 不授权开始 T029 implementation、T030、Wave 14 Integration、下一 Wave，亦不处理
  DG-05 或 DG-08。

## 后续批准补充

ADR 0029 为 T030 的 `REPORT` category binding 将 Provider protocol 显式升级为
`AGENT_PROVIDER_EXECUTION_V2`。V1 的其他执行、安全、限制、retry、SSE 与 composition 规则继续有效；
V1 response 不得被静默解释为 V2。
