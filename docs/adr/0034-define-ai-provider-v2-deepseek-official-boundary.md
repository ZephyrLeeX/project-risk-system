# 定义 AI Provider V2 与 DeepSeek Official 边界

状态：已批准（用户于 2026-08-17 明确授权建立本 addendum）

## Context

AI Agent V2 产品需求将首版 Provider 收敛为 DeepSeek 官方，并要求把旧 `ai_provider_configs` 的单行 endpoint/model/key 模型重构为 Provider Account 与 Model Config 两层。该决定与 ADR 0005 的可配置公网/内网 OpenAI-compatible Provider，以及 ADR 0028 的单 Provider execution snapshot/固定内部 JSON protocol 存在冲突。

本 addendum 只解除 T048（V2 Task 1）的实现门禁，不设计或授权 Scope Guard、native Agent Tool Loop 执行、Interaction、Mutation 或其他 Task 2–5 行为。

## Decision

### Provider 类型与扩展边界

- AI Agent V2 V1 的 production registry 只允许 `DEEPSEEK_OFFICIAL`。
- `AiProviderAdapter` 是 Agent Core 将来消费的厂商无关边界；Core-facing request/response、tool call、token usage 与错误均使用 Provider-neutral typed DTO。
- V1 只实现 `DeepSeekOfficialAdapter`。`CompanyApiAdapter`、OpenAI 或其他 adapter 仅是未来扩展方向，本 Task 不提供实现、选择项、空壳或 fallback。
- `DEEPSEEK_OFFICIAL` 固定指 DeepSeek 官方 `https://api.deepseek.com`。管理员不能设置 origin、base URL 或任意公司 endpoint；adapter 仅访问该 origin 下的 `/models` 与 `/chat/completions`，并继续经过已批准的 outbound DNS/IP/rebinding guard；实际 socket 连接固定到 guard 已验证并即时 revalidate 的 IP，同时保留官方 hostname 作为 HTTP Host、TLS SNI 与证书校验名。
- Admin 配置的 `modelName` 原样作为模型 ID 使用；代码不提供业务默认 model name。

### Account / Model 分层与 secret

- `AiProviderAccount` 保存 account identity、`providerType`、encrypted API key、key version/mask、enabled 与 account health；一个 Account 对应多个 `AiModelConfig`。
- `AiModelConfig` 保存 `modelName`、enabled、isDefault、priority、timeout、model health 与配置错误；API key 不出现在 Model 层。
- secret 继续通过 ADR 0014 / T007 的 versioned `SecretCipher` 边界加密。数据库、API、OpenAPI、日志、audit、错误、Redis/Celery payload 均不保存或返回明文 key/Authorization。
- T048 的候选快照持有 account/model 的不可变调用参数与 encrypted credential envelope/reference；只在 adapter 调用边界解密到进程内存，不把明文 key 传入 Core-neutral request DTO。
- 旧 `ai_provider_configs` 与既有 runtime 保留供 mail/weekly/legacy Agent 使用；V2 不读它、不 backfill、不自动迁移、不 dual-write。旧 AiCallLog 关系保持兼容；V2 metadata log 使用明确的 account/model references 或 snapshots。

### DeepSeek transport 与 normalization

- `DeepSeekOfficialAdapter.list_models()` 使用官方 `GET /models`；chat 使用官方 `POST /chat/completions`。
- adapter 接受 Provider-neutral messages/tools/tool results，内部转换为 DeepSeek/OpenAI-compatible Chat Completions wire payload。
- `choices`、`message.tool_calls`、`finish_reason`、HTTP status/header/body 等原始 DeepSeek 字段只存在于 adapter 内部；adapter 输出 normalized assistant text、typed tool calls、normalized finish reason 与 token usage。
- native tool calls 是 transport 能力；本 Task 只验证传输与 normalization，不实现 Agent Tool Loop 或执行任何业务 Tool。
- response 是不可信输入：128 KiB body hard limit、UTF-8/JSON/object/schema 严格校验；raw response body 不持久化、不写日志或错误。

### 稳定候选快照

- 候选查询只包含 `Account.enabled=true`、`Model.enabled=true`、account/model 当前可用的记录。
- 稳定顺序固定为：`Model.isDefault DESC` → `Model.priority ASC` → `Model.id ASC`。`id` 是最终稳定 tiebreaker。
- 每次调用/未来 Agent turn 开始时在单一数据库读取边界生成 immutable tuple snapshot。之后的 Admin priority/default/enable/health 修改不改变该 snapshot；新调用读取新顺序。
- 同一 Account 最多一个 enabled default model，由 PostgreSQL partial unique constraint 保证。没有 default 时仍按 priority/id 选择；不得硬编码模型补位。

### Retry / failover 分类

- 单模型 transport retry 是 bounded：默认最多 2 次 retry（合计 3 attempts），每次使用该 Model 的固定 timeout，指数退避 `base * 2^(attempt-1)` + jitter；`Retry-After` 只在可解析且不超过 10 秒时参与等待。
- retryable 且允许 failover：network、connect/read timeout、HTTP 429、HTTP 408、明确 transient HTTP 5xx。先在当前模型耗尽 retry，才切换 snapshot 中下一模型。
- 不做同模型 retry、但允许直接 failover：HTTP 404/model not found；同时把该 Model Config 标为配置问题。
- 禁止 failover：HTTP 401/403、HTTP 400/其他非 404 的 4xx、malformed/schema、protocol implementation、业务/tool/RBAC、internal programming error。
- HTTP 401/403 标记 Account credential/config issue 并终止本次候选遍历。
- 一次 429/5xx/network/timeout 只记录瞬时 metadata，不永久把 Account 或 Model 标为 `FAILED`；连接测试可显式更新测试健康，404 与 401/403 可更新对应配置健康。
- 全部候选耗尽时返回 typed aggregate unavailable error，仅含安全 metadata，不拼接 raw body/prompt/tool payload。

### Health 分离

- Account health 表示 credential/account 配置：`UNTESTED | AVAILABLE | CREDENTIAL_ERROR`。
- Model health 表示模型配置/枚举可用性：`UNTESTED | AVAILABLE | CONFIG_ERROR`。
- 候选中的 “available” 排除 `CREDENTIAL_ERROR` account 与 `CONFIG_ERROR` model；`UNTESTED` 可候选，避免单次测试未执行即永久停服。
- 成功 `/models` 或 chat 可恢复相关 health 为 `AVAILABLE`；404 只污染 Model，401/403 只污染 Account；transient error 不永久污染任一 health。

### Admin V2 additive contract

- 旧 `/api/admin/ai-services/**` 保持不变。
- V2 使用 additive `/api/admin/ai-provider-v2/accounts` 与嵌套 `/accounts/{accountId}/models` surface，包含 account CRUD/status/key rotation/test/models discovery，以及 model CRUD/status/default/test 所需端点。
- DTO 只暴露 masked key、health、timestamps 与安全错误 code；绝不返回 encrypted envelope、key version material 或原始 Provider body。
- 所有 mutation 继续使用 `admin.ai.manage`、标准 `ApiResponse` envelope、traceId、typed audit 与现有错误形状。

## Supersession scope

- 对 AI Agent V2/T048–T052，本文替代 ADR 0005 中“管理员可配置任意公网/内网 OpenAI-compatible endpoint”的 Provider 选择语义；mail/weekly 与 legacy runtime 仍按 ADR 0005 运行，直到后续 Task 有零引用证据并明确迁移/清理。
- 对 AI Agent V2，本文替代 ADR 0028 中单个 legacy `provider_config_id`/endpoint/model snapshot 与 Provider transport retry 分类；ADR 0028 的 durable execution、SSE、secret/logging、restricted tools 与无直接 mutation 安全不变量继续有效，直到后续 V2 ADR 明确替代。
- ADR 0014、0015 的 encryption、outbound security、additive Alembic 与 PostgreSQL-only 要求继续有效。

## Consequences

- T048 可以新增 Account/Model schema、adapter、Admin V2 backend/OpenAPI、migration、fake HTTPS tests、selection/retry/failover/health tests。
- T049 只能消费本 Task 冻结的 Provider-neutral contract 与 immutable candidate snapshot；不得把 DeepSeek wire format拉回 Agent Core。
- T048 不改变现有前端 consumer，不实现 Company API，不执行真实业务 Tool，不迁移或 dual-write legacy Provider。
