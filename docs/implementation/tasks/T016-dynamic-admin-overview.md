# T016 — Implement dynamic management overview
- **Task ID:** T016
- **Title:** Implement dynamic management overview
- **Status:** READY
- **Objective:** Replace fixed admin health, attention and audit activity with one approved dynamic backend contract.
- **Design baseline:** Design §§3,9,10(4).
- **Authoritative source references:** ADRs 0019 and 0023; `project-risk-system/apps/web/src/views/admin/AdminDashboardView.vue`; existing admin/import/provider/audit API modules.
- **Relevant ADR IDs:** 0003, 0006, 0008, 0011.
- **Dependencies:** T008, T011, T012, T013, T014, T015, T018, T024.
- **Scope:** Overview endpoint/service aggregating API/DB/Redis/worker/external checks, actionable counts and recent audit/import facts.
- **Explicit out-of-scope:** New UI (T033), monitoring platform, synthetic success.
- **Expected read set:** Named view/modules and addendum.
- **Expected write set:** Python admin overview module/OpenAPI/tests.
- **Contracts/invariants:** Health reflects real checks with bounded timeout; no secrets; attention links to real resolvable facts.
- **Acceptance criteria:** Approved schema and healthy/degraded/unavailable tests pass.
- **Validation:** API integration with dependency fault injection.
- **Required deliverables:** Endpoint/service/schemas/tests.
- **Stop conditions:** An ADR 0019 section cannot be satisfied through the declared source modules.
- **Known integration risks:** Slow external health checks and false aggregate “healthy”.

## DESIGN_GAP RESOLUTION

ADR 0023 已补足 `/api/admin/overview` 的 `health`、`attention`、`recentAudit` item schema、状态、排序、link、分段不可用和错误语义。T016 可在不自行补设计的前提下实施；实现前仍须按 Task 读取 ADR 0023，并对 health dependency fault injection、分段权限和 partial-data contract 进行独立验证。
