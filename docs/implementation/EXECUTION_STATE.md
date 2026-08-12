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
- Current Wave: Wave 11 IN_PROGRESS.
- T026: DESIGN_GAP DG-12 (ADR 0025 does not approve a Provider-visible risk-category selection/mapping contract; no T026 implementation has started).
- T042: BLOCKED by DG-04; not started.
- DG-01: RESOLVED by ADR 0019
- DG-02: RESOLVED by ADR 0018
- DG-03: RESOLVED by ADR 0019
- DG-06: RESOLVED by ADR 0020
- DG-07: RESOLVED by ADR 0020
- DG-09: RESOLVED by ADR 0021
- DG-10: RESOLVED by ADR 0022
- DG-11: RESOLVED by ADR 0024
- DG-12: OPEN (T026 Provider risk-category selection/mapping contract)

Checkpoint commits:
- T025 design: ad37f90a6ae0cb643a19c09d9c91b02b8eacbad7
- T025: ec403f99c606ba7c6ff429b0f375c9cc85d04439
- Wave 10 integration fix: `7b9722075ca3bc8358789198b7ef6b0e6282fcfa`
- Wave 10 final checkpoint: `7b9722075ca3bc8358789198b7ef6b0e6282fcfa` (metadata recorded in the following report commit)
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
- T026 implementation attempt stopped at DG-12: `MAIL_PROVIDER_DERIVED_CONTENT_V1` has no approved category option/mapping field, yet T026 candidates require a validated local `categoryId`. No code, migration, API, test, T042, Wave 11 Integration or next-Wave work was started.
- T024 implementation and Independent Review: `REVIEW_PASSED`; see `docs/implementation/reports/T024.md` and `docs/implementation/reports/WAVE-09-PARTIAL.md`.
- T024 checkpoint: `597a43b830e2e639d17b775c069a8fbfb896efd4`.
- T019 implementation, Independent Review and validation are complete; checkpoint is recorded in `docs/implementation/reports/T019.md`.
- T019 checkpoint: `4ddf34e`.
- T020 checkpoint: `8a0297ba1eaa5d72432a442cbda746d0ff480075`.
- T016 checkpoint: `cbd5569869cb36c5b7ae93645edeeecfbfb49842`; see `docs/implementation/reports/T016.md` and `docs/implementation/reports/WAVE-10-PARTIAL.md`.
- Wave 9 final Integration checkpoint: created after recording the PASS result and test baseline maintenance in `docs/implementation/reports/WAVE-09.md` and this state file.
