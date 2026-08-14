# T045 — Restore `_Contract` serialization-mode OpenAPI schema fidelity

- **Task ID:** T045
- **Title:** Restore `_Contract` serialization-mode OpenAPI schema fidelity
- **Status:** `REVIEW_PASSED`（contract-fidelity remediation 完成；code checkpoint SHA 见 `reports/T045.md`/`EXECUTION_STATE.md`）
- **design/metadata checkpoint:** `c95efd6c043d380edd993703e712fb4de59eeb0d`
- **Objective:** Fix the duplicated `_Contract` wildcard field-serializer so FastAPI serialization-mode OpenAPI schema generation expresses the real ADR-approved field types, then re-freeze the OpenAPI authority and generated frontend types — without changing any runtime JSON behavior or API surface.
- **Type:** Contract-fidelity remediation (cross frozen write-set, explicitly authorized).
- **Design baseline:** ADR 0011（OpenAPI 为唯一契约权威，生成产物不得手工修改）、ADR 0021（weekly-report schema）、ADR 0023（admin overview item schema）、ADR 0028（agent execution schema）、ADR 0029（agent REPORT category schema）。
- **Authoritative source references:** T033 DESIGN_GAP report (`docs/implementation/reports/T033.md`)；T032 OpenAPI export tooling (`apps/api-python/src/risk_platform/openapi_export.py`，frozen `packages/contracts/openapi/openapi.json`，`packages/contracts/src/generated/openapi.ts`)；三个 `_Contract` 定义点。
- **Relevant ADR IDs:** 0011, 0021, 0023, 0028, 0029.
- **Dependencies:** T016（`REVIEW_PASSED`，owns `admin/overview/schemas.py`）、T027（`REVIEW_PASSED`，owns `weekly_reports/schemas.py`）、T029（`REVIEW_PASSED`，owns `agent/schemas.py`）、T032（`REVIEW_PASSED`，owns OpenAPI export/freezing tooling 与 frozen artifacts）。
- **Scope:** 仅修正三个 `_Contract` 基类的 datetime/runtime 序列化实现，使 serialization-mode JSON schema 恢复真实字段类型；re-freeze OpenAPI 权威与生成类型；扩展 schema-fidelity 测试。
- **Explicit out-of-scope:**
  - 不重开 T016/T027/T029/T030/T032（已完成 Task 写集冻结于 `REVIEW_PASSED`；T045 以新 Task 身份接管 `_Contract` 文件的 fidelity 修复）。
  - 不改变任何 ADR-approved 字段名/类型/枚举/必填性（0021/0023/0028/0029 的字段定义不变）。
  - 不改变 API surface（path/method/envelope/字段名不变；re-freeze 仅补回此前缺失的字段类型信息）。
  - 不合并三个 `_Contract` 副本为共享基类（超出最小 fidelity 修复范围；保持 in-place 最小修正）。
  - 不执行 T033/T034 前端 cutover。
  - 不处理 DG-05/DG-08。
- **Expected read set:** 三个 `schemas.py` 的 `_Contract` 及其子类；`openapi_export.py`；`tests/test_openapi_export.py`；三个模块既有序列化/schema 测试；frozen `openapi.json`/`openapi.ts`。
- **Expected write set:**
  - `apps/api-python/src/risk_platform/admin/overview/schemas.py`（仅 `_Contract` 基类）
  - `apps/api-python/src/risk_platform/weekly_reports/schemas.py`（仅 `_Contract` 基类）
  - `apps/api-python/src/risk_platform/agent/schemas.py`（仅 `_Contract` 基类）
  - `packages/contracts/openapi/openapi.json`（re-freeze）
  - `packages/contracts/src/generated/openapi.ts`（`pnpm contracts:gen` 重新生成，不得手工编辑）
  - `apps/api-python/tests/test_openapi_export.py`（扩展 schema-fidelity 断言）
  - 必要时三个模块的序列化行为测试（断言 runtime JSON 不变）
- **Contracts/invariants:**
  - **Runtime JSON 兼容：** datetime 字段仍序列化为 UTC RFC 3339 毫秒 + `Z`（`isoformat(timespec="milliseconds").replace("+00:00","Z")`）；`extra="forbid"` 保留；既有 runtime 序列化测试全部通过。
  - **Serialization-mode schema 恢复：** `model_json_schema(mode="serialization")` 与导出的 OpenAPI 中，所有 `_Contract` 子类字段必须表达真实类型/枚举/`$ref`，不得出现裸 `{}` 或生成 `unknown`。覆盖至少：`AdminOverview`/`HealthItem`/`AttentionItem`/`RecentAuditItem`/`UnavailableSection`/`OverviewLink`（ADR 0023）、`WeeklyReportResponse`/`WeeklyProjectDetail`/`WeeklyReportItemResponse`/`WeeklyProjectSummary`/`WeeklyProject`（ADR 0021）、`AgentConversationResponse`/`AgentConfirmationResponse`/`AgentConversationEnvelope`/`AgentMessageEnvelope`/`AgentConversationHistory`/`AgentMessagePage`/`AgentToolHelp`/`AgentHelpResponse`/`AgentToolResult`（ADR 0028/0029）。
  - **生成产物权威：** FastAPI → `openapi.json` → `openapi.ts` 单向流向不变；`openapi.ts` 由 `pnpm contracts:gen` 生成，不得手工修改（ADR 0011）。
  - **无 API surface 改变：** re-freeze 前后 path/method/operation 数量不变（仍 93 paths）；差异仅限于此前损坏 schema 的字段类型补全。
- **Acceptance criteria:**
  1. 三个 `_Contract` 的 wildcard `field_serializer("*", when_used="json", check_fields=False)` 被 fidelity-safe 实现替代（如 `model_serializer(mode="wrap")`、按字段 `field_serializer`、或 `Annotated[datetime, PlainSerializer(...)]` 类型别名，由 Implementer 选择最小实现），runtime JSON 行为不变。
  2. `model_json_schema(mode="serialization")` 对上述全部 `_Contract` 子类表达真实字段类型（无 `unknown`/裸 `{}`）。
  3. `risk-platform-openapi` 重新导出的 `openapi.json` 与 frozen artifact 同步；`pnpm contracts:gen` 重新生成的 `openapi.ts` 表达真实字段类型。
  4. `pnpm contracts:check` 在 clean working tree 下 exit 0（zero diff）。
  5. `@risk-platform/contracts` 与 `@risk-platform/web` typecheck exit 0。
  6. Ruff / mypy / focused pytest（含扩展的 schema-fidelity 断言）/ `uv lock --check` / `git diff --check` 全部 PASS。
  7. OpenAPI export 确定性：连续 3 轮 export+gen zero diff。
  8. 既有 overview/weekly/agent runtime 序列化测试全部通过（datetime 格式、`extra="forbid"` 不变）。
- **Validation:** Ruff、mypy、`tests/test_openapi_export.py`（扩展）、三个模块 focused pytest、`pnpm contracts:check`、contracts/web typecheck、`uv lock --check`、`git diff --check`、export+gen 确定性。
- **Required deliverables:** 修正后的 `_Contract` 实现、re-frozen `openapi.json`/`openapi.ts`、schema-fidelity 测试。
- **Stop conditions:**
  - 保持 runtime JSON 兼容需改变某 ADR-approved 字段形状 → `DESIGN_GAP`。
  - fidelity 修复无法在不合并 `_Contract` 或触碰非 `_Contract` 代码的前提下完成 → `DESIGN_GAP`。
  - re-freeze 后 path/method/operation 数量或既有兼容契约发生变化（非纯字段类型补全）→ `DESIGN_DEVIATION`。
- **Known integration risks:** Pydantic v2 serialization-mode schema 与 `model_serializer`/`PlainSerializer` 的交互；re-freeze 引入非预期 schema diff；三个副本需一致修正。
- **Postconditions:** T033、T034 在 T045 `REVIEW_PASSED` 后恢复可执行（解除 block）。
