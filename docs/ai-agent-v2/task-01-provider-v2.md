# Task 1 — Provider V2 + DeepSeek Official Adapter

# Status

`NOT_STARTED`

# Goal

建立 Provider Account 与 Model Config 分层的 V2 配置模型，实现唯一 production adapter `DeepSeekOfficialAdapter`，并向后续 Agent Core 暴露稳定、厂商无关的候选模型快照和明确错误分类。

# Prerequisites

- 完整阅读需求文档 §§2–6、48、51–53、`implementation-plan.md`、`progress.md` 和本文件。
- Orchestrator 已将本计划恰好 5 个主 Task 一一登记/映射到 `TASK_GRAPH.md` 中的 5 个 `Txxx`，且本 Task 对应的 `Txxx` 是当前唯一 assigned Task。
- 阅读 ADR 0005、0014、0015、0028 及当前 `ai_providers`、Agent configuration snapshot、Admin Provider API/OpenAPI 实现。
- Provider V2 ADR/addendum 已批准，明确替代 ADR 0005/0028 中与 DeepSeek-only、Account/Model 分层和 model failover 冲突的部分。
- 核对 planning checkpoint、Git 状态与 Alembic 当前 head。

若 Task Graph activation 或 ADR gate 未满足，状态改为 `BLOCKED`，记录相应 `ACTIVATION GATE`/`DESIGN_DEVIATION` 后停止，不得开始 adapter 或 migration。

# Scope

- 定义厂商无关 `AiProviderAdapter` interface、request/response DTO 与 typed provider errors。
- 新增 `ProviderAccount` 和 `ModelConfig` 持久化、repository/service、加密 key 边界及健康状态。
- V1 仅实现 `DEEPSEEK_OFFICIAL`；官方 base origin/path 不允许被管理员替换为公司网关。
- 实现 DeepSeek 官方 `/models` 枚举/连通性与 `/chat/completions` transport，包括 native tool-call message 形状。
- 实现模型选择：enabled、available、default 优先、priority ASC、id ASC；一次 turn 使用不可变候选顺序快照。
- 单模型有限 transport retry 与跨模型 failover 分类：network/timeout/429/明确 transient 5xx/404 可切换；401/403/400/schema/protocol/business/tool/RBAC/internal 不切换。
- 404 标记对应 Model Config 配置问题；401/403 标记 Account credential/config 问题。
- 提供 V2 Admin backend API/OpenAPI 能力；精确 path/DTO 需在批准 addendum 中冻结并保持现有 `/api` 兼容。
- 保留旧 Provider 数据，不自动映射或双写；明确 legacy read boundary。

# Non-goals

- 不实现 Agent scope、intent、tool loop、tool execution 或 conversation execution。
- 不修改 Agent 前端或 Admin 前端；Admin UI 归 Task 5。
- 不实现 Company API、OpenAI 或其他 provider adapter；不得用空壳假成功。
- 不迁移旧 Company/OpenAI-compatible 配置为 `DEEPSEEK_OFFICIAL`。
- 不删除旧 Provider runtime path；只有 Task 5 在零引用证据下清理。
- 不提前实现 Task 2–5。

# Current implementation impact

- `risk_platform.ai_providers.models.AiProviderConfig` 当前把 vendor/endpoint/protocol/model/key/strategy 合并在单行。
- `AiProviderClient` 当前支持 Chat Completions、Responses、Anthropic，并被 Agent、mail/weekly 等路径复用。
- `AgentExecutionConfig` 当前只快照单个 provider/model；Task 2 才消费 V2 候选快照。
- Admin Provider API/UI 和 generated OpenAPI 目前围绕单层配置；本 Task 只完成后端 additive contract，UI 延后。
- shared composition 当前拥有 `AgentProviderAdapter`，后续应只负责依赖组装，不拥有厂商业务规则。

# Backend

- Provider domain 内聚 API/schema/service/repository/models/adapter/tests，避免把 DeepSeek 分支写进 Agent Core。
- Adapter 输入输出必须是 Core-neutral DTO；原始 DeepSeek payload 只存在于 adapter 内存。
- `/models` 返回值只作为可用性与 model ID 依据，不成为业务事实。
- 每 turn 的候选 snapshot 固化 account/model IDs、显示快照、timeout 与安全所需的 encrypted key reference/snapshot；具体密钥快照策略必须符合 ADR 0014 和批准 V2 ADR。
- 记录 metadata-only call logs：account/model/latency/token/error classification；不记录 prompt、messages、tool arguments/results、response body 或 key。
- 更新 `.env.example` 或批准等价模板，仅当新增非 secret 配置项。

# Frontend

本 Task 不修改前端。必须提供足够的 OpenAPI contract 和 fixture 供 Task 5 接入；现有 Admin 页面继续可构建，不得因 additive backend 变更而失效。

# Database impact

- 预计新增 Provider Account、Model Config 以及必要健康/约束字段的单一 Alembic revision；最终表名、enum 与关系由批准 ADR 固定。
- 一个 Account 1:N Model；模型排序稳定；同一 Account 只能存在符合批准规则的默认模型约束。
- key 仅存加密字段与 key version；不得存明文。
- `ai_provider_configs` 与其数据保留为 legacy，不 dual write、不 backfill、不转义 vendor。
- PostgreSQL fresh upgrade、约束、enum、FK、index 和 downgrade/forward policy 必须验证；不设计 online migration。

# API / Contract impact

- 新增/调整 Admin V2 Provider Account、Model、models refresh/test、enable/default/priority 所需 API；必须通过 OpenAPI 描述。
- 保持通用 response envelope、traceId、时间与 error shape。
- 不破坏现有 conversation API。
- 若必须移除/改变现有 Provider API，属于 breaking change，必须先获得明确批准；默认采用 additive V2 surface 并由 Task 5 切换消费者。

# Security requirements

- 官方 endpoint 固定为批准的 DeepSeek official origin，禁止以 `DEEPSEEK_OFFICIAL` 保存任意 host。
- API key 加密、轮换、遮罩、日志/异常/API 不回显；请求 Authorization 不得进入日志。
- 出站仍经 SSRF/DNS/IP/rebinding policy；不得因官方 adapter 放宽 mailbox 或其他 provider policy。
- Provider response 是不可信输入，严格限制 body、UTF-8、JSON 和 schema。
- 错误必须按需求分类，不得把 401/403/400/protocol bug 当 transient failover 掩盖。

# Tests

- PostgreSQL migration/model constraint tests：Account 1:N Model、稳定排序、默认/enable/health 约束、legacy 保留。
- fake HTTPS DeepSeek server：`/models`、`/chat/completions`、tool-call envelope、token usage、body limit。
- retry/failover matrix：network/connect/read timeout、429、500/502/503、其他批准 transient 5xx、404、401、403、400、invalid schema。
- turn snapshot stability：Admin 并发改 priority/default 后，当前 turn 候选顺序不变，新 turn 使用新顺序。
- secret/outbound/log negative tests。
- 现有 mail/weekly/provider 非 Agent regression tests。

# Acceptance Criteria

1. production registry 中仅有 `DEEPSEEK_OFFICIAL` adapter；Company API 不可选择或调用。
2. DeepSeek 官方 `/models` 和 `/chat/completions` 通过 fake server contract tests，且 Core-facing DTO 无 DeepSeek 字段。
3. 选择排序与 turn snapshot 在 PostgreSQL 并发测试中完全符合需求。
4. failover matrix 每个状态都有客观断言，non-failover 错误不会调用下一模型。
5. 旧 provider 行保持不变且无 dual write；V2 Agent selection 不读取 legacy 行。
6. key、Authorization、prompt、tool/result 和 raw response 不出现在 API/log/audit/call log。
7. fresh database 可迁移到单一 head，OpenAPI 与 backend contract 一致。
8. 没有 Agent loop、interaction、mutation 或前端越界改动。

# Quality Gates

- 读取当前 `mise.toml`/`pyproject.toml` 后使用仓库配置版本运行 `uv lock --check`、Ruff、mypy。
- 运行本 Task focused pytest、Provider/mail/weekly regressions和 PostgreSQL migration tests。
- 运行 OpenAPI export/check；若 generated artifact 按当前仓库规则需同步，则只同步 contract artifact，不改前端消费逻辑。
- `git diff --check`。
- 检查 diff 仅含批准 write-set、文档/契约/migration/config template，且无 secret。

# Independent Review

Reviewer 必须独立核对：需求 §§2–6/48/51–53、批准 V2 ADR、模型排序/failover、DeepSeek official endpoint、secret/outbound policy、legacy preservation、migration 和 API compatibility。必须特别搜索 Company adapter、硬编码业务默认 model、Agent Core 中的 DeepSeek 条件和双写。结论只允许 `REVIEW_PASSED` 或带 findings 的 `REVIEW_FAILED`。

# Completion Deliverables

- Provider V2 ADR/addendum reference 和 implementation report。
- Account/Model schema + migration + services/Admin API。
- `AiProviderAdapter` contract 与 `DeepSeekOfficialAdapter`。
- selection snapshot、retry/failover、health 与 security tests。
- OpenAPI/config template 必要更新。
- Task checkpoint commit；本文件/README/progress 状态更新。

# Handoff to Next Task

Task 2 只消费已冻结的 adapter、candidate snapshot 和 typed errors。交接必须列出公开 Python interfaces、schema/API、migration head、测试 fixture、checkpoint SHA 和遗留 legacy path。Task 1 完成后停止，不自动开始 Task 2。
