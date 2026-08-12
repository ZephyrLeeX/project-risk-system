# 定义邮件 Provider 风险分类映射契约

状态：已批准

## Context

ADR 0025 已把 T026 的 Provider 输入限制为版本化的派生邮件内容，并要求 Provider 的项目选择只使用
本次请求的 opaque option。正式风险与邮件候选都要求一个有效本地 `categoryId`，但 ADR 0025 没有定义
Provider 可见的分类集合、其返回值或本地映射。因此不能安全地让 Provider 猜测分类，也不能从 NestJS
历史 payload 推导该选择。

本 ADR 只解决 DG-12。它不改变 `/api` mailbox review contract、T022 风险/待办/时间线单事务、ADR 0018
durable-task 语义、ADR 0022 cursor，或 ADR 0025 的邮件内容最小化、日志和出站边界。

## Decision

### 版本化分类选项集合

T026 的风险提取请求改用 `MAIL_PROVIDER_DERIVED_CONTENT_V2`。它包含 ADR 0025 的全部允许字段和限制，
并额外且只能额外包含 `risk_category_options`；`MAIL_PROVIDER_DERIVED_CONTENT_V1` 不包含分类契约，
不得用于产生可发布候选。

每次 Provider 调用前，Worker 在当前事务读取 `isActive=true` 的 `RiskCategory`，按
`sortOrder ASC, code ASC, id ASC` 建立仅驻留内存的集合 `RISK_CATEGORY_OPTIONS_V1`。每项为：

- `option_id`：本次请求连续、不透明的 `C1`、`C2`……；
- `name`：本地类别的当前 `name`；
- `description`：当前 `description`，无值时为 `null`；
- `default_level`：当前 `defaultLevel`，无值时为 `null`。

不得发送本地 category UUID、`code`、关键词、颜色、排序、启停状态、历史分类或任何不在上述 projection
中的字段。分类集合为空时不调用 Provider，AI/review stage 以 `PERMANENT_FAILURE`
`NO_ACTIVE_RISK_CATEGORY` 结束。

### Provider 输出和确定性本地映射

Provider 输出的每个风险候选必须恰好包含一个 `category_option_id`，其值必须精确等于本次
`risk_category_options` 中的一个 `option_id`。Provider 只能在该集合中选择；不得输出 category UUID、
code、name、自由文本 taxonomy、数组、置信度驱动的替代分类或任何自行生成的分类值。

T026 使用本次内存集合的精确一对一映射，将 `category_option_id` 映射到当次读取的本地 `categoryId`。
分类名、描述和 `default_level` 仅帮助 Provider 选择，均不改变本地风险 level：候选的 `level` 仍必须是
既有正式枚举 `HIGH`、`MEDIUM` 或 `LOW`，并按 T026 的严格输出 schema 验证。`UNKNOWN` 不是可发布
候选的 level。

持久化 candidate 或经 review 确认发布前，T026 必须在同一数据库事务重新验证映射目标仍存在且
`isActive=true`。随后只向已有 T022 风险创建服务传入映射后的本地 UUID；不得复刻风险、待办、时间线
或审计写规则。

### 拒绝、fallback 和 retry

输出缺失分类、包含多个或非标量分类值、使用未知/过期 option、试图以 name/code/自由文本分类，或使
一个候选的分类无法唯一映射时，整个 Provider 输出均为结构化 `PERMANENT_FAILURE`
`PROVIDER_INVALID_OUTPUT`。mail-stage 只可另存固定子原因
`MISSING_CATEGORY_OPTION`、`AMBIGUOUS_CATEGORY_OPTION` 或 `UNKNOWN_CATEGORY_OPTION`；不得保存原始
输出或不可信分类文本。不会部分接受同一 response 中的其他候选。

不存在默认类别、keyword 分类、最近历史类别、人工预设或 legacy payload fallback。若请求后、候选
持久化前本地目标被删除或停用，则结果为 `RETRYABLE_FAILURE` `CATEGORY_MAPPING_STALE`；ADR 0018 的
durable retry 必须重新抓取、重新解析、重新过滤并以最新活动类别重建整份 V2 payload。达到既有尝试
上限时仍按 ADR 0025 记为 `PERMANENT_FAILURE` `PROVIDER_RETRY_EXHAUSTED`。

### 兼容性、留存与可观察性

`MAIL_PROVIDER_DERIVED_CONTENT_V2` 与 `RISK_CATEGORY_OPTIONS_V1` 是成对版本。新版本只能通过新 ADR
引入；实现不得静默接受未知 schema/mapping version，也不得把 V1 response 解释为 V2。每个 Provider
attempt 的 option-to-local mapping 仅在该 attempt 内存存在，durable retry 不复用它。

ADR 0025 的禁止留存规则继续适用。除其已批准的字段外，mail-stage 可保存
`MAIL_PROVIDER_DERIVED_CONTENT_V2`、`RISK_CATEGORY_OPTIONS_V1`、当前 system-config release 的
`version`、选项数量、结果状态及固定 failure/subreason code；不得保存 option 清单、UUID 映射、类别
name/description、prompt、Provider response 或可重建内容。`ai_call_logs` 与 audit 仅记录已有安全
Provider/trace/result 字段及固定 schema/mapping version、选项数量和固定 failure code；审计不接收
任意 metadata。

Provider 内部网络/连接、超时、429、5xx 的 retry 分类和调用 trace 仍完全遵循 ADR 0025。分类 schema
错误不可 retry；映射陈旧只按上述 durable retry 处理。

## Consequences

- Provider 能在受控、有限、版本化的本地分类 projection 中选择，正式系统以确定性 mapping 取得合法
  `categoryId`。
- 无法解释、过期或自由生成的分类 fail closed，不产生模拟、默认或部分接受的候选。
- T026 可恢复为 `READY`，但本 ADR 不实现任何代码、migration、API 或测试。
