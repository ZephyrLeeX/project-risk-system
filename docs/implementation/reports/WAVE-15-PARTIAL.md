# Wave 15 Partial Report

## 当前状态

- Wave 15：`IN_PROGRESS`
- T030 DESIGN_GAP resolution：`PASS`
- T030：`REVIEW_PASSED`
- Independent Review：`REVIEW_PASSED`
- Integration：未启动

## Readiness resolution

ADR 0029 已批准 Agent `REPORT` category contract：唯一权威来源为 PostgreSQL active
`RiskCategory`；复用 `RISK_CATEGORY_OPTIONS_V1` 的 attempt-local opaque mapping；服务端将映射所得
`categoryId` 与分类 revision 绑定进 canonical preview；preview 签发和 confirmation 均重新校验，
missing、disabled、stale、unknown 与 legacy binding 全部 fail closed。

已确认 T030 的 T004、T006、T010、T021、T022、T029 依赖未发生变化且均处于稳定完成状态，因此 Wave 15
已标记为 `IN_PROGRESS`，仅开始 T030。T040、Integration、下一 Wave、DG-05 和 DG-08 均未启动或处理。

## T030 实施与审查

T030 candidate 首次独立审查为 `REVIEW_FAILED`；其后按限定 scope 完成 remediation，并重新独立审查：

- 复用既有领域服务（`RisksService.create_in_session` / `resolve_in_session`、
  `TodosService.process_in_session`）与 caller-owned transaction。
- `RISK_CATEGORY_OPTIONS_V1` projection 严格使用 ADR 0026 的 `option_id` / `default_level`。
- `PROCESS` / `RESOLVE` 显式 `categoryOptionId: null` 经 `model_fields_set` 判定 fail closed。
- `AGENT_REPORT_CATEGORY_STALE` 完成 durable retry → rebuild → exhaustion 闭环。
- 补齐 API/legacy fail-closed、permission/scope recheck、stale/disabled/missing category、
  retry/rebuild/exhaustion、并发 one-use、replay、mutation + metadata-only audit 同事务回滚的
  PostgreSQL 16 acceptance。

二次独立审查为 `REVIEW_PASSED`。focused PostgreSQL 16 + Redis 7 acceptance `10 passed`；Ruff、mypy、
`uv lock --check`、`git diff --check` 全部通过。T030 已 `REVIEW_PASSED`，code checkpoint
`933fb2549dbda304e1675a755786249f33c0e547`。

Wave 15 保持 `IN_PROGRESS`；Integration 未启动，未进入下一 Wave，DG-05 / DG-08 未处理。
