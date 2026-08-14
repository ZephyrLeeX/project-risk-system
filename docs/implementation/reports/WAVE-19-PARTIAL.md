# Wave 19 Partial Report (T045)

## 结果

- Wave 19：`IN_PROGRESS`（仅 T045 完成；Integration 未启动）
- T045：`REVIEW_PASSED`（code checkpoint `ac351cb8aed4ccfd35a13fdf4becbdc2e80f22df`）
- schema-fidelity：`PASS`（三个 `_Contract` serialization-mode schema 恢复真实字段类型，受影响 contract 字段不再降级为 `unknown`）
- Independent Review：`REVIEW_PASSED`（无 blocking finding）
- Wave 19 Integration：未启动
- T033 / T034：仅同步 readiness 为 `READY`（metadata，未执行）
- 下一 Wave（Wave 20 = T033/T034）：未启动
- DG-05 / DG-08：未处理

## 范围

Wave 19 为单工作单元 Wave，仅授权 T045（contract-fidelity remediation：恢复 `_Contract` serialization-mode OpenAPI schema fidelity 并 re-freeze authority）。T045 的 blocking 前置（T016/T027/T029/T032 均 `REVIEW_PASSED`）已满足，无新 blocker。本轮授权为跨 frozen write-set 的最小 fidelity 修复（T016/T027/T029 的 `_Contract` 文件 + T032 的 OpenAPI artifacts）。

## readiness 确认

- T016：`REVIEW_PASSED`
- T027：`REVIEW_PASSED`
- T029：`REVIEW_PASSED`
- T032：`REVIEW_PASSED`（Wave 18 Integration `PASS`）
- 无新 blocker。Wave 19 标记 `IN_PROGRESS`，仅授权 T045。

## 根因与修复摘要

三个模块（`admin/overview`、`weekly_reports`、`agent`）共享的 `_Contract` 基类其通配 `field_serializer("*", when_used="json", check_fields=False)` 携带 `-> object` 返回注解，Pydantic 将该返回注解用作每个字段的 serialization-mode JSON schema，使全部 `_Contract` 子类字段在 frozen OpenAPI authority 中退化为 `unknown`。修复为仅删除返回注解（+ `# type: ignore[no-untyped-def]`），序列化函数体逐字节不变，runtime JSON 兼容（datetime→UTC RFC 3339 毫秒+`Z`、`when_used="json"`、`extra="forbid"` 不变）。详见 `reports/T045.md`。

## validation（全部 PASS）

- Ruff / mypy（185 source files）
- focused pytest（`test_openapi_export.py` `14 passed`；overview/weekly/agent focused `21 passed, 26 skipped`）
- full backend regression（PostgreSQL 16 + Redis 7）：`283 passed, 1 skipped`
- `@risk-platform/contracts` / `@risk-platform/web` typecheck：exit 0
- OpenAPI compatibility check：PASS（85/85 frontend calls，无 breaking diff）
- export+gen 确定性：3 轮 byte-identical
- fresh OpenAPI surface：93 paths / 243 schemas / 104 unique operations（不变）
- `uv lock --check`（58）/ `git diff --check` / `pnpm contracts:check`（post-commit，exit 0）

## 受影响 contract

- ADR 0023：`AdminOverview`/`HealthItem`/`AttentionItem`/`RecentAuditItem`/`UnavailableSection`/`OverviewLink`
- ADR 0021：`WeeklyReportResponse`/`WeeklyProjectDetail`/`WeeklyReportItemResponse`/`WeeklyProjectSummary`/`WeeklyProject`
- ADR 0028/0029：`AgentConversationResponse`/`AgentConfirmationResponse`/`AgentConversationEnvelope`/`AgentMessageEnvelope`/`AgentConversationHistory`/`AgentMessagePage`/`AgentToolHelp`/`AgentHelpResponse`/`AgentMessageResponse`/`AgentToolResult`

全部恢复真实字段类型；T033/T034 所需字段在 generated `openapi.ts` 中均具有真实类型（非 `unknown`）。

## 未执行项（按本次授权边界）

- 未执行 T033 / T034。
- 未启动 Wave 19 Integration。
- 未启动下一 Wave。
- 未处理 DG-05 / DG-08。

Wave 19 保持 `IN_PROGRESS`；T033/T034 同步为 `READY`，可在 Wave 20 评估/执行。
