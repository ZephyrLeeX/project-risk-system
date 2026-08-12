# Execution State

Design baseline: <commit>
Plan baseline: <commit>

Completed waves:
- Wave 1: PASS
- Wave 2: PASS
- Wave 3: PASS
- Wave 4: PASS
- Wave 5: PASS
- Wave 6: PASS
- Wave 8: PASS
- Wave 9: PASS (T018/T024 integration validation and test baseline maintenance)
- Wave 10: PASS (T016/T019/T020/T025 integration validation complete)
- Wave 11: PASS (T026/T042 integration validation and test baseline maintenance complete)
- Wave 12: PASS (T027/T031 Integration validation complete; no integration fix)
- Wave 13: PASS (T028 Integration validation complete; no integration fix; T029 not executed)
- Wave 14: PASS (T029 Integration validation complete; T030 not executed)

Completed tasks:
- T001 REVIEW_PASSED
- T002 REVIEW_PASSED
- T003 REVIEW_PASSED
- T005 REVIEW_PASSED
- T006 REVIEW_PASSED
- T007 REVIEW_PASSED
- T009 REVIEW_PASSED
- T008 REVIEW_PASSED
- T010 REVIEW_PASSED
- T041 REVIEW_PASSED
- T004 REVIEW_PASSED
- T011 REVIEW_PASSED
- T015 REVIEW_PASSED
- T017 REVIEW_PASSED
- T021 REVIEW_PASSED
- T023 REVIEW_PASSED
- T027 REVIEW_PASSED
- T031 REVIEW_PASSED
- T028 REVIEW_PASSED
- T029 REVIEW_PASSED

Current:
- Wave 6: PASS (T004/T008/T010 remain REVIEW_PASSED; PostgreSQL integration validation passed)
- Wave 7: PASS (T011/T012/T013/T014/T015/T017/T021 REVIEW_PASSED; T006/T013 remediation and T012 regression fixture fix validated)
- T004: REVIEW_PASSED
- T008: REVIEW_PASSED
- T010: REVIEW_PASSED
- T011: REVIEW_PASSED
- T013: REVIEW_PASSED
- T014: REVIEW_PASSED
- T012: REVIEW_PASSED
- T015: REVIEW_PASSED
- T017: REVIEW_PASSED
- T021: REVIEW_PASSED
- T022: REVIEW_PASSED
- T023: REVIEW_PASSED
- Wave 8: PASS (T022 import-cycle fix reviewed; full pytest and PostgreSQL/Alembic integration validation passed)
- Current Wave: Wave 10 PASS (T016/T019/T020/T025 REVIEW_PASSED; Integration PASS); Wave 9 complete (T018/T024 REVIEW_PASSED; Integration PASS).
- Wave 10 design resolution: ADR 0023 supplies the missing T016 admin overview item contract. No Wave 10 Integration or next-Wave work was started. DG-04/DG-05/DG-08 remain out of scope.
- T019: REVIEW_PASSED (selected as the single Wave 10 work unit; see `docs/implementation/reports/T019.md`).
- T020: REVIEW_PASSED (collection/department remediation, Independent Review and PostgreSQL 16 focused validation complete; see `docs/implementation/reports/T020.md`).
- T025: REVIEW_PASSED (ADR 0024 attachment safety implementation, Independent Review and validation complete; code checkpoint `ec403f99c606ba7c6ff429b0f375c9cc85d04439`).
- T016: REVIEW_PASSED (ADR 0023 admin overview implementation, Independent Review and PostgreSQL 16 focused validation complete; code checkpoint `cbd5569869cb36c5b7ae93645edeeecfbfb49842`).
- T018: REVIEW_PASSED (checkpoint `b8a5a5f`; see `docs/implementation/reports/T018.md`).
- T024: REVIEW_PASSED (DG-10 resolved; implementation and Independent Review complete; checkpoint `597a43b830e2e639d17b775c069a8fbfb896efd4`).
- Wave 9 Integration: PASS; the six stale test-baseline assertions were updated only in tests, followed by full validation. No production redesign or next-Wave task started.
- Wave 10 Integration: PASS; cross-module/full validation and PostgreSQL 16/Alembic validation complete. Minimal integration fix changed T025 helper IPC from Queue to Pipe; no safety boundary was widened. See `docs/implementation/reports/WAVE-10.md`.
- Current Wave: Wave 12 PASS. T027 is REVIEW_PASSED (code checkpoint `8beeb062069ddc5dd6104e0b80178fcb90da9e3b`). T031 is REVIEW_PASSED (code checkpoint `6545fdaf6ad4029c044c2338cc2fb36b7e385b03`). Wave 12 Integration PASS; no integration fix. Wave 11 PASS (T026/T042 REVIEW_PASSED; Integration PASS).
- Current Wave: Wave 15 `NOT_STARTED`; T030 is `READY` after ADR 0029 resolved its REPORT category `DESIGN_GAP`. Wave 14 is PASS; T029 is REVIEW_PASSED (code checkpoint `b57e831e68e47cdfba43f78285d10481c15000e1`). T040 production composition write-set remains untouched. DG-05 and DG-08 remain out of scope.
- T026: REVIEW_PASSED (Celery worker isolation remediation complete. PostgreSQL 16 + Redis + real Celery `solo` worker acceptance/negative tests pass: `16 passed`; each worker handler asserts the random Alembic-created temporary PostgreSQL schema. `register_executor` is app-local (`shared=False, lazy=False`), preventing stale executor/handler/factory leakage across test Celery apps. Independent Review passed; no dispatcher direct execution was used as worker proof. Checkpoint: `76c5ef6cb50705b63ad86e7a9b05d00bf9a45da4`.)
- T042: REVIEW_PASSED (approved retention configuration, frozen facts, hold persistence/API, terminal-state trigger, metadata-only audit and lock-ordered protection recheck complete. PostgreSQL 16 focused validation `17 passed`; Independent Review passed. Code checkpoint: `d6652c82529c2d2902a5f476d225e582b38ebaf3`. See `docs/implementation/reports/T042.md`.)
- DG-01: RESOLVED by ADR 0019
- DG-02: RESOLVED by ADR 0018
- DG-03: RESOLVED by ADR 0019
- DG-04: RESOLVED by ADR 0027
- DG-06: RESOLVED by ADR 0020
- DG-07: RESOLVED by ADR 0020
- DG-09: RESOLVED by ADR 0021
- DG-10: RESOLVED by ADR 0022
- DG-11: RESOLVED by ADR 0024
- DG-12: RESOLVED by ADR 0026
- DG-13: RESOLVED by ADR 0021 immutable received-time addendum
- DG-14: RESOLVED by ADR 0028
- DG-15: RESOLVED by ADR 0029

Checkpoint commits:
- T029 design: `51074211d3fd388514c4c8405b32295a9dd214cc`
- T029: `b57e831e68e47cdfba43f78285d10481c15000e1`
- T031: `6545fdaf6ad4029c044c2338cc2fb36b7e385b03`
- T027: `8beeb062069ddc5dd6104e0b80178fcb90da9e3b`
- T025 design: ad37f90a6ae0cb643a19c09d9c91b02b8eacbad7
- T025: ec403f99c606ba7c6ff429b0f375c9cc85d04439
- Wave 10 integration fix: `7b9722075ca3bc8358789198b7ef6b0e6282fcfa`
- Wave 10 final checkpoint: `7b9722075ca3bc8358789198b7ef6b0e6282fcfa` (metadata recorded in the following report commit)
- Wave 11 final checkpoint: `4487c09a2281c462e3b4c93e0553080a56af4531` (metadata recorded in the following report commit)
- Wave 12 final checkpoint: `286dbab0dca17870434a2bc7e5ddac79b2f9109f` (metadata recorded in this report commit)
- Wave 13 final checkpoint: `841a38ce37c8e34bff513b72f6236d64303d9b6b` (metadata recorded in the following report commit)
- T010: e30dd45
- T004: 1b4cfa8
- T011: baa3208
- T012: 7361d26
- T013: 13463f3
- T014: d9bde24
- T015: dd603dc
- T017: 7c4b2e60f1b54ad11c18caeae6a65a440026e9db
- T021: ae2dc4e

Architecture changes:
- ADR 0017: Audit is metadata-only; no snapshot/redaction.
- ADR 0018: Durable tasks use PostgreSQL task/outbox facts, domain-to-task references, fenced leases and at-least-once dispatch.
- ADR 0019: Agent API, confirmation and PostgreSQL-backed SSE contract.
- ADR 0020: Agent Celery execution and domain-command contract.
- ADR 0021: PostgreSQL weekly aggregate lifecycle contract.
- ADR 0022: Mail fetch-to-parse/AI UID/UIDVALIDITY-only durable handoff, stage terminal states, crash recovery and cursor contract.
- ADR 0023: Admin overview health, attention, recent-audit item and partial-data contract.
- ADR 0024: Mail attachment type allowlist, parser isolation, resource limits, metadata-only outcomes and ADR 0022 retry handoff.
- ADR 0025: Mail source-refetch-to-Provider versioned derived-content, redaction, size, retry and metadata-only observability contract for T026.
- ADR 0026: Mail Provider risk-category option projection, deterministic local mapping, fail-closed classification and compatibility contract for T026.
- ADR 0028: Agent execution task/configuration snapshot, restricted Provider protocol, invalid-output, retry/timeout/cancellation and PostgreSQL SSE fact boundary for T029.
- ADR 0029: Agent REPORT active-category authority, reused opaque option projection, canonical category binding, confirmation revalidation, retry/audit and compatibility contract.

Environment:
- Python validation via mise explicit tool selection.
- PostgreSQL integration tests required where applicable.

Important invariants:
- FastAPI is production backend.
- NestJS reference-only.
- PostgreSQL only.
- No dual write.
- Human-facing reports Chinese.

Integration blockers:
- T013 regression test fixture was minimally corrected and passes independently; no production code change.
- T006 migration portability remediation explicitly installs pgcrypto in `public` and calls `public.digest`; isolated PostgreSQL upgrade/hash regression passes with `search_path` excluding `public`.
- Full Ruff and mypy pass after the T013-only remediation.
- The T012 role update regression (`422` caused by an obsolete `code` field in the update test fixture) was fixed by updating the fixture only; no production code or approved semantics changed.
- Current Wave: Wave 7 is PASS; T011, T012, T013, T014, T015, T017 and T021 have been completed in this execution unit sequence.
- T017 implementation, Independent Review, validation and checkpoint commit are complete. T021 remains READY and was not started.
- T021 implementation, Independent Review and validation are complete. Wave 7 Integration Fix passed; Wave 8 remains not started. See `docs/implementation/reports/WAVE-07.md`.
- Wave 7 final integration checkpoint includes the T012 fixture-only regression fix; Wave 8 was then started by the Orchestrator.
- Wave 8 Integration Fix: T022 package `__init__` eager re-export was removed to resolve the collection cycle; regression tests, full pytest, PostgreSQL 16 and Alembic validation passed. See `docs/implementation/reports/T022.md` and `docs/implementation/reports/WAVE-08.md`.
- T022 checkpoint: `a91d203`.
- T023 checkpoint: `d721895f241d2eb7d1a8ee446cd189c3842a4c74`.
- T022 Quality Fix checkpoint: `ba339789409dc8138f763c6325262e9cff1be319`.
- Wave 8 prior failed-integration checkpoint: `9c1b598d425a06cb3ed6753252d9209e6a73aaae`.
- Wave 8 final Integration Fix checkpoint: `5f5d421f5bc4f7a86c0f70c1febda6c1c8ae515a`; see `docs/implementation/reports/WAVE-08.md`.
- T018 checkpoint: `b8a5a5f`; see `docs/implementation/reports/T018.md` and `docs/implementation/reports/WAVE-09-PARTIAL.md`.
- DG-10 design checkpoint: created by ADR 0022 and synchronized task/design state; T024 implementation is complete and reviewed.
- DG-11 design resolution: ADR 0024 approves the attachment safety policy and returns T025 to READY. No T025 implementation, Wave 10 Integration or next-Wave work was started; DG-04/DG-05/DG-08 remain out of scope.
- T026 design resolution: ADR 0025 approves `MAIL_PROVIDER_DERIVED_CONTENT_V1`, including the only permitted derived text, deny-list, filtering, limits and retry/log/audit boundary. T026 is READY; no implementation, T042, Wave 11 Integration or next-Wave work has started. DG-04/DG-05/DG-08 remain out of scope.
- T026 design checkpoint: `56368827d42406dc73fc371dfa31db2a03a3c096`.
- DG-04 design resolution: ADR 0027 approves bounded, versioned retention configuration; frozen import/conversation expiry and rollback-protection facts; an auditable hold state machine; and fail-closed deterministic cleanup/backup-copy predicates. T042 is READY. No T042 implementation, Wave 11 Integration or next-Wave work has started; DG-05 and DG-08 remain out of scope.
- DG-04 design checkpoint: `c744b6a889f09be71ff088c3b9f4ba0892c73c9a`.
- DG-12 design resolution: ADR 0026 approves `MAIL_PROVIDER_DERIVED_CONTENT_V2` plus `RISK_CATEGORY_OPTIONS_V1`, one opaque classification choice, deterministic active-local-category mapping, fail-closed invalid/ambiguous handling and retry/audit boundaries. T026 is READY; no implementation, T042, Wave 11 Integration or next-Wave work has started. DG-04/DG-05/DG-08 remain out of scope.
- T024 implementation and Independent Review: `REVIEW_PASSED`; see `docs/implementation/reports/T024.md` and `docs/implementation/reports/WAVE-09-PARTIAL.md`.
- T024 checkpoint: `597a43b830e2e639d17b775c069a8fbfb896efd4`.
- T019 implementation, Independent Review and validation are complete; checkpoint is recorded in `docs/implementation/reports/T019.md`.
- T019 checkpoint: `4ddf34e`.
- T020 checkpoint: `8a0297ba1eaa5d72432a442cbda746d0ff480075`.
- T016 checkpoint: `cbd5569869cb36c5b7ae93645edeeecfbfb49842`; see `docs/implementation/reports/T016.md` and `docs/implementation/reports/WAVE-10-PARTIAL.md`.
- Wave 9 final Integration checkpoint: created after recording the PASS result and test baseline maintenance in `docs/implementation/reports/WAVE-09.md` and this state file.
- T042 execution stop: candidate implementation validation passed on isolated PostgreSQL 16, but Independent Review is `REVIEW_FAILED`. ADR 0027 requires a T042 human hold management surface without an approved public URL/envelope/error contract; this is `DESIGN_GAP` under T042's stop condition. The candidate additionally needs a single lock ordering and non-reactivatable terminal hold enforcement. Wave 11 remains `IN_PROGRESS`; no T042 checkpoint, Integration, DG-05, DG-08 or next-Wave work was started.
- T042 DESIGN_GAP resolution: ADR 0027 addendum now approves the human hold create/release/query HTTP contract, fixed metadata-only audit boundary, exact retry/conflict outcomes, resource advisory/fact/hold PostgreSQL lock ordering, and database-enforced non-reactivatable terminal states. T042 is `READY` only; the retained candidate is neither accepted nor code-checkpointed and requires a fresh implementation/review cycle. Wave 11 remains `IN_PROGRESS`; no Integration, next-Wave, DG-05 or DG-08 work was started. Design checkpoint: `ee4de76840f9181caf3158a448cb45af9949112d`.
- Wave 11 Integration: `PASS`; stale PostgreSQL test baselines now include T042 frozen import facts, Alembic head `20260812_0006` and `retention_holds_lifecycle_guard`. Cross-module `37 passed`, full pytest `204 passed, 1 skipped`, Ruff, mypy, lock, diff and PostgreSQL 16/Alembic validation all pass. Wave 12, T027 and T031 were not started; DG-05/DG-08 were not handled. See `docs/implementation/reports/WAVE-11.md`.
- Wave 12 readiness: `READY`. T027 and T031 dependencies are complete and their applicable design gaps are resolved. T031 retains a stale `BLOCKED_DESIGN_GAP (DG-04/DG-10) / TODO` header despite the approved resolutions, so T027 was selected as the single unambiguous READY task. Wave 12 is `IN_PROGRESS`; T031, DG-05 and DG-08 remain untouched.
- T027 execution stop: `DESIGN_GAP`. ADR 0021 and the frozen design require `sent_at`, falling back to immutable `received_at`, for Shanghai-week ownership, but `MailMessage` has no `receivedAt` and the approved handoff metadata persists only `sent_at`. Substituting `createdAt` or `processedAt` would make delayed synchronization change week ownership. T027 excludes new mail storage and explicitly stops when the required source service is unavailable. No production/test implementation, Independent Review, validation, code checkpoint, T031 work, Wave 12 Integration, DG-05 or DG-08 work was performed. See `docs/implementation/reports/T027.md` and `docs/implementation/reports/WAVE-12-PARTIAL.md`.
- Wave 12 design/metadata sync: T031's stale `BLOCKED_DESIGN_GAP (DG-04/DG-10) / TODO` header was corrected to `TODO` after confirming ADR 0027/addendum, ADR 0022 and completed T042 satisfy those blockers; T031 was not executed. ADR 0021's approved immutable received-time addendum resolves DG-13 with IMAP `INTERNALDATE`, a frozen first-durable-observation fallback, UTC `timestamptz(3)` facts, migration/backfill ownership and database-enforced retry/refetch stability. T027 is restored to `READY`; T031 remains `TODO`; Wave 12 remains `IN_PROGRESS`. No Task implementation, Integration, next Wave, DG-05 or DG-08 work was started.
- T027 implementation: `REVIEW_PASSED`. Weekly aggregate/query/invalidation/reconciliation and ADR 0021 immutable received-time ingestion/migration are implemented. Two independent reviews passed; Ruff, mypy, lock, diff and isolated PostgreSQL 16 focused validation (`65 passed`) are green. Code checkpoint: `8beeb062069ddc5dd6104e0b80178fcb90da9e3b`. T031 remains `TODO`; Wave 12 remains `IN_PROGRESS`; Integration, next Wave, DG-05 and DG-08 were not started.
- T031 implementation: `REVIEW_PASSED`. Auditable import-source, Agent-conversation and bounded orphan-temp cleanup is implemented with ADR 0027 lock-ordered protection rechecks, durable `RETENTION_CLEANUP` task/outbox creation, dry-run reporting, two-phase filesystem tombstone recovery and fail-closed partial-failure handling. Independent Review passed after storage/split-brain regressions were remediated. Ruff, mypy, lock, diff and isolated PostgreSQL 16 focused validation (`40 passed`, including Alembic `upgrade head`/`check`) are green. Code checkpoint: `6545fdaf6ad4029c044c2338cc2fb36b7e385b03`. Wave 12 remains `IN_PROGRESS`; Integration, next Wave, DG-05 and DG-08 were not started.
- Wave 12 Integration: `PASS`; cross-module focused pytest `66 passed`, full pytest `234 passed, 1 skipped`, Ruff, mypy, lock, diff and PostgreSQL 16/Alembic validation all pass. No integration fix was required. T028, next Wave, DG-05 and DG-08 were not started. See `docs/implementation/reports/WAVE-12.md`.
- Wave 13 readiness: `READY`. T028 dependencies T004/T010/T014/T020/T022/T026/T027 are complete, and ADR 0012/0013/0019/0021 provide the required conversation, retention, authorization, and weekly-report read contracts. DG-05/DG-08 remain out of scope. Wave 13 is `IN_PROGRESS`; only T028 is authorized in this work unit.
- T028 implementation: `REVIEW_PASSED`. Agent conversations/API, owner-scoped history, frozen retention metadata, closed read-only domain-tool registry and provenance metadata are implemented. Ruff, mypy, `uv lock --check`, `git diff --check` and isolated PostgreSQL 16 focused tests (`2 passed`) are green. Full-suite extra attempt reached `61 passed, 1 skipped` before an existing Redis connection wait was interrupted; no T028 failure was observed. See `docs/implementation/reports/T028.md` and `docs/implementation/reports/WAVE-13-PARTIAL.md`. Code checkpoint: `21cb6a9e541dfed311b33f2355c6e8358ba6dda3`.
- Wave 13 Integration: `PASS`; T028 Agent conversation/tool registry 与 Dashboard、Risk、Todo、Weekly Report 的授权和 scope 路径联合验证 `27 passed`，完整 PostgreSQL 16 + Redis 7 pytest `236 passed, 1 skipped`。Ruff、mypy、`uv lock --check`、`git diff --check` 及 PostgreSQL 16 空库 Alembic `upgrade head`/`check` 均通过；无 integration fix。T029 未执行，未启动下一 Wave，DG-05/DG-08 未处理。详见 `docs/implementation/reports/WAVE-13.md`。
- Wave 14 readiness: `READY`。T029 dependencies T004/T007/T008/T010/T014/T028 均已完成，ADR 0019/0020 提供 SSE/Worker 总体边界；Wave 14 已标记为 `IN_PROGRESS`，仅授权 T029。
- T029 execution stop: `DESIGN_GAP`。ADR 0020 要求 Agent invocation 使用 durable task/outbox，但没有定义新增的 Agent execution task kind、稳定 idempotency/retry/timeout definition 或 execution configuration identifier 的权威持久化来源；现有 `DurableTaskKind`/PostgreSQL enum/registry 也没有该 kind，而 ADR 0018 禁止未登记自由 kind。ADR 0020 还未定义不可信 Provider 的 intent/tool-call/arguments/text-delta/preview proposal protocol 及 malformed-output 到 SSE error 的映射。选择任一结构都会自行扩张安全/契约边界，故未实施、未审查、未执行 PostgreSQL 16/SSE validation，未创建 code checkpoint。T030、Wave 14 Integration、下一 Wave、DG-05/DG-08 均未处理。详见 `docs/implementation/reports/T029.md` 与 `docs/implementation/reports/WAVE-14-PARTIAL.md`。
- T029 DESIGN_GAP resolution: ADR 0028 approves `AGENT_EXECUTION`, a PostgreSQL immutable execution-configuration snapshot and the closed `AGENT_PROVIDER_EXECUTION_V1` request/response protocol. It fixes Provider output validation, retry/timeout/cancellation, and the PostgreSQL-backed SSE/metadata-only boundary. T029 is `READY` only; no T029 implementation/review/validation, T030, Wave 14 Integration, next-Wave, DG-05 or DG-08 work was started. Design checkpoint: `51074211d3fd388514c4c8405b32295a9dd214cc`.
- T029 remediation: `DESIGN_DEVIATION`. Production Celery registration requires shared worker composition (session factory, Provider adapter and handler registry) in T040's exclusive FastAPI/Celery bootstrap write-set. Per the task stop condition, remediation stopped before further implementation, Independent Review or real worker/SSE acceptance; this is not merely `ENVIRONMENT_BLOCKED`. Existing candidate changes remain uncheckpointed. T030, Integration, next Wave, DG-05 and DG-08 untouched.
- T029 DESIGN_DEVIATION resolution: ADR 0028's approved composition-ownership addendum makes the boundary executable. T029 must deliver an explicit dependency-injected module-local `AGENT_EXECUTION` handler mapping and prove it with an isolated Celery app, T008 `register_executor`, a real test worker and fake Provider; it must not edit or register the shared production FastAPI/Celery composition root. T040 alone constructs production session/Provider/tool-registry dependencies, merges module handler mappings and registers the shared Celery executor exactly once. T029 is restored to `READY` without resuming implementation. Wave 14 remains `IN_PROGRESS`; T030, Integration, next Wave, DG-05 and DG-08 remain untouched.
- T029 composition-ownership design/metadata checkpoint: `db3d4889353d71452f1d5e6affaed84930bc8415`.
- T029 implementation: `REVIEW_PASSED`. Closed raw Provider/tool/preview validation, fail-closed durable execution, PostgreSQL ordered SSE resume, cancellation/retry/attempt-wide heartbeat/backpressure and module-local isolated real-worker acceptance are complete. Independent Review passed after two remediation rounds. Ruff, mypy, lock, diff, Alembic and PostgreSQL 16 + Redis 7 + real Celery/SSE focused validation (`27 passed`) are green. Code checkpoint: `b57e831e68e47cdfba43f78285d10481c15000e1`. T040 shared production composition was not modified; T030, Wave 14 Integration, next Wave, DG-05 and DG-08 remain untouched.
- Wave 14 Integration: `PASS`. Full PostgreSQL 16 + Redis 7 validation completed with `243 passed, 1 skipped`; Ruff, mypy, `uv lock --check`, `git diff --check` and Alembic head `20260812_0008` all pass. Integration-only fixes updated T029 schema metadata expectations and made the weekly-report reconciliation fixture wall-clock independent. T030 remains unstarted, the next Wave remains unstarted, and DG-05/DG-08 remain untouched. See `docs/implementation/reports/WAVE-14.md`. Final checkpoint is recorded after this entry.
- Wave 15 / T030 readiness: `DESIGN_GAP`. The approved Agent `REPORT` canonical/command contract has no `categoryId` or approved category mapping, while the formal `Risk` schema and T022 `RiskCreate` require a valid category. The operation therefore cannot be expressed through the existing domain services without inventing a default, inference rule or contract extension. Per T030's stop condition, Wave 15 was not marked `IN_PROGRESS`; implementation, Independent Review, validation and code checkpoint were not started. T040, Integration, next Wave, DG-05 and DG-08 remain untouched. See `docs/implementation/reports/T030.md` and `docs/implementation/reports/WAVE-15-PARTIAL.md`.
- T030 DESIGN_GAP resolution: ADR 0029 approves PostgreSQL active `RiskCategory` as the sole `REPORT.categoryId` authority, reuse of `RISK_CATEGORY_OPTIONS_V1` with one opaque Provider choice, server-side mapping plus canonical category revision binding, and fail-closed locked confirmation revalidation. T030 is restored to `READY`; Wave 15 remains `NOT_STARTED`. No T030 implementation/review/validation, T040, Integration, DG-05 or DG-08 work was started. Design/metadata checkpoint: pending.
