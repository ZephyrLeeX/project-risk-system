# 定义 Agent `REPORT` 风险分类绑定契约

状态：已批准

## Context

ADR 0019 的 confirmation canonical content 与 ADR 0020 的 `REPORT` command 没有
`categoryId`，但正式 `Risk` 与 T022 `RiskCreate` 都要求一个有效本地分类。ADR 0028 的
`AGENT_PROVIDER_EXECUTION_V1` 也没有向 Provider 提供受控分类选项。T030 因而无法在不发明默认
分类、文本推断或新写模型的情况下调用既有领域服务。

本 ADR 只解决 T030 的该 `DESIGN_GAP`。它复用 ADR 0026 已批准的分类 projection，不改变风险
taxonomy、权限、项目范围、两态 lifecycle 或 T022 transaction，不实现 T030，不启动 Wave 15，
也不处理 DG-05/DG-08。

## Decision

### 权威来源与受控选项

- `REPORT.categoryId` 的唯一权威来源是确认所属 PostgreSQL 数据库中当前存在且
  `RiskCategory.isActive=true` 的本地行。不得由 Provider、客户端、prompt、历史 payload 或
  NestJS reference 直接提供本地 UUID。
- Agent 复用 ADR 0026 的 `RISK_CATEGORY_OPTIONS_V1`：在 `RESPOND` Provider round 前读取活动分类，
  按 `sortOrder ASC, code ASC, id ASC` 排序，投射连续 opaque `C1`、`C2`……以及 `name`、
  `description`、`default_level`。不得投射 UUID、code、keywords、颜色、排序值、启停状态或历史分类。
- 该 projection 与 option-to-local-UUID mapping 只存在于当前 Agent execution attempt 的内存中；
  不新增分类副本、默认分类、keyword inference、最近历史分类或 legacy fallback。分类集合为空仍可让
  Provider 返回普通文本，但不得签发 `REPORT` preview。

### Provider proposal 与 preview binding

- 修改后的请求/响应协议版本固定为 `AGENT_PROVIDER_EXECUTION_V2`；不得静默改变或继续接受 V1
  response。V2 的 `RESPOND` request 在 V1 字段之外恰好新增
  `riskCategoryOptions: {schema: "RISK_CATEGORY_OPTIONS_V1", items: [...]}`；`PLAN` request 不含该字段。
  request size、round/action/tool limits 及其他安全规则仍完全沿用 ADR 0028。
- V2 `REPORT` 的 `preview_proposal.content` 必须包含且只可包含一个
  `categoryOptionId`，精确匹配本次 option 集合；不得包含 `categoryId`、category name/code、自由文本
  taxonomy、数组或 fallback。`PROCESS`/`RESOLVE` 不得包含 category option。
- 服务端严格验证 proposal 后，将 opaque option 一对一映射为本地 `categoryId`。签发 token 前，在同一
  PostgreSQL transaction 重新读取并以 `FOR SHARE` 锁定目标分类，确认其仍存在且 active，并计算
  `categoryBindingDigest`：对 `{categoryId, updatedAt, name, description, defaultLevel}` 使用 ADR 0019
  canonical JSON 后取 SHA-256。任何 projection 字段或 `updatedAt` 的变化都会形成新 binding。
- ADR 0019 canonical content 增加固定字段 `categoryId` 与 `categoryBindingDigest`：`REPORT` 两者必填，
  值为上述服务端映射和 digest；`PROCESS`/`RESOLVE` 两者固定为 `null`。`categoryOptionId` 永不进入
  canonical content、confirmation token、SSE preview 或数据库。`contentDigest`、`idempotency_key` 与
  preview event 绑定扩展后的 canonical content，因此分类与其他业务字段同等防篡改。

### Fail-closed、确认与并发

- 缺少、多个、非标量或未知 `categoryOptionId` 是非 retryable
  `AGENT_PROVIDER_INVALID_OUTPUT`；不产生 token、preview 或业务写入。活动分类集合为空时若 Provider
  仍提出 `REPORT`，同样按未知 option 失败。
- option 映射后、preview 签发前，目标分类已删除、disabled 或 binding 改变，固定为 retryable
  `AGENT_REPORT_CATEGORY_STALE`。本次不签发 token；仅可按 ADR 0028 的既有
  `AGENT_EXECUTION` retry budget 重跑完整 attempt，并从 PostgreSQL 重建最新 option 集合。不得复用旧
  mapping；耗尽后沿用 durable-task retry-exhausted 终态。
- confirmation transaction 先锁定 token 并完成 owner、expiry、content/scope/permission 检查，再按
  canonical `categoryId` 以 `FOR SHARE` 锁定分类，重新计算 binding，最后才调用 T022 领域服务。分类
  missing、disabled 或 digest 不一致统一返回 `409 AGENT_CONFIRMATION_CONTENT_MISMATCH`，整个 transaction
  不消费 token、不写 risk/todo/timeline；同一 token 的后续尝试继续 fail closed 直至过期，客户端必须
  通过新的 Agent execution 获取新 preview。
- category row 的 `FOR SHARE` 锁保持到 risk/todo/timeline/audit 与 token success result 同一 transaction
  提交，阻止并发配置更新使已校验分类在写入前失效。成功重试继续以 token 的既有
  `idempotency_key` 返回原 risk，不重复创建。

### Audit、retry 与兼容性

- 成功 confirmation 继续使用 `AGENT_REPORT_CONFIRMED`，主 resource 仍是 risk；不增加任意 audit
  metadata。确认阶段分类失效使用既有 `AGENT_CONFIRMATION_CONTENT_MISMATCH` failure code，且不得记录
  option 列表、mapping、分类名称/描述、canonical content、prompt 或 Provider output。
- Agent execution 的 durable task、AiCallLog 与应用日志只可额外记录固定
  `AGENT_PROVIDER_EXECUTION_V2`、`RISK_CATEGORY_OPTIONS_V1`、option count 和固定 failure code；不得
  持久化 option 集合、UUID mapping 或 `categoryBindingDigest` 的输入。
- 部署后不得执行缺少 `categoryId`/`categoryBindingDigest` 的 legacy `REPORT` token；它按
  `AGENT_CONFIRMATION_CONTENT_MISMATCH` fail closed。既有 `PROCESS`/`RESOLVE` canonical 仅增加两个固定
  `null` 字段，业务语义不变。公开 confirm endpoint、空 request、成功 envelope、TTL、owner/scope、
  replay 与 SSE event envelope 均不变；公开 preview `content` 的 additive 字段由 T032/T034 生成并消费。

## Consequences

- T030 可在其限定 write-set 内补充 V2 category projection/preview normalization，并通过现有 T022 服务
  实现 category-bound `REPORT` confirmation；无需默认分类、自由 taxonomy、独立写模型或 migration。
- T030 必须覆盖 empty/unknown/stale/disabled/missing category、confirmation revalidation、并发配置更新、
  legacy token、retry/idempotency/audit 与现有 `PROCESS`/`RESOLVE` compatibility 测试。
- T030 恢复为 `READY`；本 ADR 不授权实现、不把 Wave 15 标记为 `IN_PROGRESS`，也不授权 T040。
