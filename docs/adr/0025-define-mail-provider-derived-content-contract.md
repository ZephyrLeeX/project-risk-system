# 定义邮件 Provider 派生内容契约

状态：已批准

## Context

ADR 0022 要求 T026 在每次执行和 durable retry 时按
`(mailbox_config_id, uid_validity, imap_uid)` 重抓 source，且 task payload 不能携带
raw source。ADR 0024 已确定 T025 的安全解析格式和资源边界，但没有确定风险提取可向已启用
Provider 发送哪些从 source 派生的内容、其大小、过滤、可观察性和 retry 边界。T026 因此不能在
不自行选择邮件外发范围的情况下实施。

本 ADR 仅解决该 Provider 输入边界；不改变现有 `/api` mailbox contract、T022 的风险/待办/时间线
事务规则、ADR 0018 durable-task 基础设施、ADR 0022 cursor 规则或 ADR 0024 附件安全策略。

## Decision

### 版本化的最小 Provider 输入

T026 只可向通过 T014 选择且处于健康可用状态的 Provider 发送版本固定为
`MAIL_PROVIDER_DERIVED_CONTENT_V1` 的内存中派生对象。对象只能包含：

- `schema_version`：固定为 `MAIL_PROVIDER_DERIVED_CONTENT_V1`；
- `source_date`：邮件发送日期的 UTC `YYYY-MM-DD`；无可信日期时为 `null`；
- `project_options`：T025 已匹配项目的本次序号 `P1`、`P2`……，不含项目 UUID、名称、别名或匹配文本；
- `analysis_text`：按下文构造、过滤和截断后的文本；
- `content_stats`：仅包括 body 字符数、纳入的附件数、总字符数和 redaction 数。

Provider 输出中的项目选择只能引用 `project_options` 序号；T026 在本地映射回 project ID。没有已匹配
项目时，不调用 Provider，AI/review 阶段以结构化 `PERMANENT_FAILURE`
`NO_MATCHED_PROJECT` 终止。过滤后没有可分析文本时，同样以
`PERMANENT_FAILURE` `DERIVED_CONTENT_EMPTY` 终止。两者均不是成功或模拟候选。

`analysis_text` 是一次性的派生值，不得从 PostgreSQL 中已有的 `sanitizedSummary`、`keyPoints`、
`evidence` 或任何历史 Provider 输入回读、拼接或扩展；每次调用均从 ADR 0022 identity 重抓、按
ADR 0024 重新解析，再在当前 Worker 内存中构造。

### 可纳入与禁止的内容

- 只可纳入经 ADR 0024 清洗的 plain-text/HTML body 文本，以及状态为 `PARSED` 的固定 allowlist
  附件提取文本；不支持、超限、损坏、加密、超时、资源限制或截断的附件一律不贡献文本。
- body 最多纳入 `6,000` 个 Unicode 字符；每个附件最多纳入 `2,000` 个 Unicode 字符，最多三个
  附件；最终 `analysis_text`（含固定分段标签）最多 `12,000` 个 Unicode 字符。到达任一上限时在
  `content_stats` 以计数和 `truncated=true` 表示，绝不携带被截断部分。
- `analysis_text` 的分段标签只能是 `BODY`、`ATTACHMENT_1` 至 `ATTACHMENT_3`；不得包含 subject、
  sender name/address、收件人、抄送/密送、Message-ID、邮件地址、附件文件名、文件路径、MIME header、
  所有其他 RFC 822 header 或 handoff identity。
- 禁止向 Provider 发送 raw RFC 822 source、原始或完整 body、原始附件字节、完整附件提取文本、
  subject/sender 等 envelope 字段、持久化的内容衍生字段、内部 UUID、邮箱配置、认证信息、日志/audit
  内容、prompt 模板或任何此前的 Provider response。

### 派生、过滤和脱敏

构造 `analysis_text` 前必须按以下顺序完成：Unicode control-character 清洗、空白规范化、固定字符
截断、再进行不可逆替换。不得把被替换值保存在 payload、临时文件名、异常、日志、call log 或 audit。

- 所有 email address 替换为 `[EMAIL]`；所有 URL（含 query、fragment、userinfo）替换为 `[URL]`；
  连续 7–20 位电话/银行卡号、18 位中国居民身份证号替换为 `[NUMBER]`。
- PEM/private-key 块、`Authorization`/`Cookie`、密码、API key、token、secret、验证码及其常见
  `key=value` 或 header 形式，替换为 `[SECRET]`；命中 secret 后不得保留相邻 value。
- 过滤器无法安全处理的非法编码、控制内容或疑似凭证结构，必须丢弃所在分段，而不是原样发送。
  过滤器仅是最小化措施，不得把邮件业务文本误称为匿名化；Provider 使用仍受 T007/T014 的受限
  endpoint、凭证与出站安全约束。

### 留存、日志和审计

raw source、原始附件、解析全文、`analysis_text`、过滤前后片段、prompt 及 Provider 原始 response
只可在本次 Worker 内存和 ADR 0024 的 task 专属短生命周期临时目录中存在；不得写入 PostgreSQL、
Redis、Celery payload、task result、日志、异常、audit 或备份。

T026 可以在其 mail-stage 事实中保存 source identity、`schema_version`、Provider ID/model snapshot、
call trace ID、上述纯数值 `content_stats`、结果状态及安全 machine code；不得保存 content hash 或可
重建内容的标识。`ai_call_logs` 继续仅保存 T014 已批准的 Provider/model snapshot、scene
`RISK_EXTRACTION`、token/duration、成功/失败和安全 code/summary。审计只记录固定资源、动作、结果、
trace 和 failure code，且不接受任意 metadata 或 content 字段。

### 调用、失败与 retry

每次 Provider request 使用一个新的 trace ID 和一次性的内存 payload。Provider 配置中的有限
`retryCount` 只处理该次请求的临时网络/连接、超时、429 或 5xx；其余可恢复失败由 ADR 0018 durable
retry 处理。durable retry 必须重新按 ADR 0022 identity 抓取、重新解析、重新过滤和重新构造 payload，
不得复用内存、临时文件、payload、response 或先前 call 的内容。

Provider 返回超时、网络/连接故障、429、5xx 或明确可重试的上游故障记为
`RETRYABLE_FAILURE`；达到 ADR 0018 的尝试上限后记为 `PERMANENT_FAILURE`
`PROVIDER_RETRY_EXHAUSTED`。输出无法通过 T026 严格 schema 校验、包含未知字段、无效项目序号或
违反候选字段上限时，记为 `PERMANENT_FAILURE` `PROVIDER_INVALID_OUTPUT`，不把原始 response 记录
下来。配置缺失、禁用或不健康的 Provider 记为 `PERMANENT_FAILURE` `PROVIDER_UNAVAILABLE`。成功
只表示已得到并校验候选或空候选；发布仍须经过 T026 review 及 T022 的单事务、幂等领域服务。

所有这些 terminal/retryable 结果继续由 ADR 0022 的 AI/review stage、batch 统计和 cursor 判定消费；
本 ADR 不新增状态机或放宽 cursor 规则。

## Consequences

- T026 获得可测试的 source-refetch-to-Provider 边界，而不会把原文或附件扩散到 durable systems。
- Provider 可见的仅是有固定上限、分段、脱敏和版本的必要派生文本；subject、sender 和完整附件文本
  不会外发。
- T026 实现需要新增针对 payload allow/deny、redaction、上限、每次 retry 重抓重建、metadata-only
  call/audit、超时、无效输出及无匹配项目的测试；本 ADR 本身不实现这些改动。
