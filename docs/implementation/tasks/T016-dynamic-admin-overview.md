# T016 — Implement dynamic management overview
- **Task ID:** T016
- **Title:** Implement dynamic management overview
- **Status:** DESIGN_GAP
- **Objective:** Replace fixed admin health, attention and audit activity with one approved dynamic backend contract.
- **Design baseline:** Design §§3,9,10(4).
- **Authoritative source references:** ADR 0019; `project-risk-system/apps/web/src/views/admin/AdminDashboardView.vue`; existing admin/import/provider/audit API modules.
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

## DESIGN_GAP

ADR 0019 仅定义 `/api/admin/overview` 的顶层字段与分段权限，未定义 `health`、`attention`、`recentAudit` 的 item schema、状态枚举、排序、link target 和 unavailable/error 语义。现有前端仍使用固定数组，TypeScript contract 也没有该 API 的对应类型。新增这些公开 contract 字段将构成自行补设计，T016 保持 `DESIGN_GAP`，等待批准 addendum。
