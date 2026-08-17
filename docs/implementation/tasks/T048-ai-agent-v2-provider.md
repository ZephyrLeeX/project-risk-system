# T048 — AI Agent V2 Provider V2 + DeepSeek Official Adapter

- **Task ID:** T048
- **Mapped V2 Task:** Task 1 — Provider V2 + DeepSeek Official Adapter
- **Status:** REVIEW_PASSED
- **Objective:** 建立 Provider Account / Model Config 分层、厂商无关 `AiProviderAdapter`、唯一 production `DeepSeekOfficialAdapter`、稳定候选快照、bounded retry/failover、健康语义与 additive Admin V2/OpenAPI contract。
- **Authority:** `docs/AI Agent 重构需求说明书 v1.0.md`；`docs/adr/0034-define-ai-provider-v2-deepseek-official-boundary.md`；`docs/ai-agent-v2/task-01-provider-v2.md`。
- **Dependencies:** T014, T032, T040（均已 `REVIEW_PASSED`）。无 V2 Task 前置依赖。
- **Read set:** Task authority 明确引用的 ADR/design、现有 `ai_providers`、Agent configuration snapshot、Admin Provider API/OpenAPI、Alembic head、Provider/mail/weekly tests。
- **Write set:** `project-risk-system/apps/api-python/src/risk_platform/ai_providers/**`；必要的 composition/model registry 接线；单一 Alembic revision；Provider V2 tests 与相关 regression fixtures；OpenAPI/generated contract artifacts；本 Task/ADR/report/state/V2 progress 文档。不得修改 Vue Admin UI。
- **Acceptance / validation:** 以 `docs/ai-agent-v2/task-01-provider-v2.md` 的 Acceptance Criteria、测试矩阵、Quality Gates 与独立 Review 要求为完整 specification。
- **Non-goals:** Task 2–5 的 Scope Guard、Agent Tool Loop、Interaction、Mutation、Risk/Todo/Project 写操作、Vue UI、Company API Adapter、旧 Agent Core 删除。
- **Report:** `docs/implementation/reports/T048.md`
- **Checkpoint:** 仅在 `REVIEW_PASSED` 且全部可执行 gates 完成后创建。
