# Implementation Task Graph

## Status vocabulary

- `READY`: approved inputs are sufficient once dependencies pass.
- `BLOCKED_DESIGN_GAP`: implementation must not start until the named gap is resolved in an approved addendum/ADR and the task is updated.
- Runtime status (`TODO`, `IN_PROGRESS`, `PASS`, `FAIL`) is tracked when execution begins; all tasks here start `TODO` unless blocked.

## DAG

```mermaid
flowchart TD
 T001 --> T002
 T002 --> T003 & T005 & T007 & T008 & T009 & T023
 T003 --> T005 & T006 & T008 & T009 & T010 & T013 & T014 & T017 & T020 & T021 & T022 & T023
 T004 --> T027 & T028 & T029 & T030 & T031 & T042
 T005 --> T009 & T010 & T011 & T012
 T006 --> T009 & T011 & T012 & T013 & T014 & T015 & T017 & T021 & T022 & T023 & T026 & T030 & T031 & T041
 T007 --> T014 & T023 & T025 & T026 & T029
 T008 --> T016 & T017 & T024 & T026 & T029 & T031
 T009 --> T010
 T010 --> T011 & T012 & T013 & T014 & T015 & T017 & T020 & T021 & T022 & T023 & T027 & T028 & T029 & T030
 T011 --> T016
 T012 --> T016
 T013 --> T016 & T023 & T024 & T025 & T031 & T042
 T014 --> T016 & T026 & T028 & T029
 T015 --> T016
 T017 --> T018
 T018 --> T016 & T019 & T020
 T019 --> T031 & T042
 T020 --> T027 & T028
 T021 --> T022 & T030
 T022 --> T018 & T026 & T028 & T030
 T023 --> T024
 T024 --> T016 & T025 & T031
 T025 --> T026 & T027 & T031
 T026 --> T027 & T028
 T027 --> T028 & T034
 T028 --> T029
 T029 --> T030
 T030 --> T034
 T016 --> T033
 T041 --> T004 & T008
 T042 --> T031
 T008 & T009 & T010 & T011 & T012 & T013 & T014 & T015 & T016 & T017 & T018 & T019 & T020 & T021 & T022 & T023 & T024 & T025 & T026 & T027 & T028 & T029 & T030 & T031 --> T040
 T040 --> T032 & T035
 T032 --> T033 & T034
 T031 --> T035 & T036
 T032 --> T035
 T033 --> T035 & T037
 T034 --> T035 & T037
 T035 --> T036
 T036 --> T037
 T037 --> T038
 T038 --> T039
```

## Suggested execution waves

| Wave | Tasks | Parallelism and checkpoint |
|---|---|---|
| 1 | T001 | Python workspace and locked quality baseline checkpoint. |
| 2 | T002 | HTTP/configuration core and the module composition contract. |
| 3 | T003, T007 | DB/session foundation and crypto/outbound security consume T002 settings but use disjoint files. |
| 4 | T005, T006 | Seed and audit enforcement share the completed core schema but do not share a migration file. |
| 5 | T009, T041 | Authentication and the DG-02 durable-task migration are disjoint; T041 is blocked until DG-02 is resolved. |
| 6 | T004, T008, T010 | Capability migration, worker core and RBAC are disjoint after T041/T009 pass; T004 remains blocked on DG-01/DG-03/DG-06/DG-07. |
| 7 | T011, T012, T013, T014, T015, T017, T021 | Parallel feature submodules; each owns only its declared subpackage/tests and may consume, but not edit, shared DB/audit/task fixtures. |
| 8 | T022, T023 | Risk/todo mutation core and mailbox configuration are disjoint. |
| 9 | T018, T024 | Import commit and IMAP orchestration consume established domain/task services without modifying them. |
| 10 | T016, T019, T020, T025 | Admin overview, rollback, dashboard reads and mail parsing have disjoint module write sets; T016 is blocked on DG-01. |
| 11 | T026, T042 | Mail candidate pipeline and retention-protection policy are disjoint; T042 is blocked on DG-04. |
| 12 | T027, T031 | Weekly-report query and retention cleanup are disjoint after their respective schemas/policies pass. |
| 13 | T028 | Authorized Agent conversations/query tools. |
| 14 | T029 | SSE answer/preview contract checkpoint. |
| 15 | T030 | One-use REST confirmation transaction checkpoint. |
| 16 | T040 | Single-owner FastAPI/Celery composition and dependency-injection checkpoint. |
| 17 | T032 | Freeze OpenAPI authority and generate reproducible frontend types. |
| 18 | T033, T034 | Admin and business frontend cutovers use disjoint page/API modules and consume, but never edit, generated types. |
| 19 | T035 | Build production Compose/proxy/images only after both frontend cutovers, avoiding image-build reads racing frontend/type writes. |
| 20 | T036 | Implement and drill backup/restore against the final production volume topology. |
| 21 | T037 | Full compatibility and security suite. |
| 22 | T038 | Blocked by DG-05 until numeric performance criteria exist; capacity/reliability validation. |
| 23 | T039 | External mailbox/provider, restore, frontend E2E and Python-only production cutover evidence. |

Tasks in a wave may run concurrently only when every dependency from an earlier wave is `PASS`; a blocked task is skipped, never treated as passed. Alembic revisions are strictly serialized in the order T003 → T006 → T041 → T004 → T042. No parallel task may create an opportunistic revision. Shared app/Celery bootstrap is owned by T002 then T040; production Compose/proxy/env examples by T035; generated OpenAPI/types by T032. Feature tasks expose module-local entry points and test them with T002/T003/T008 fixtures without editing those shared files. T037/T038 record findings only and route fixes back to the owning task.

## Task catalog

| Task | Status | Direct dependencies | One-sentence objective |
|---|---|---|---|
| T001 | READY | — | Bootstrap the locked Python 3.12 backend workspace and quality gates. |
| T002 | READY | T001 | Build FastAPI configuration, HTTP envelope, tracing and baseline request security. |
| T003 | READY | T002 | Establish Prisma-equivalent SQLAlchemy models and Alembic core baseline. |
| T004 | DESIGN_GAP DG-01/DG-03/DG-06/DG-07/DG-09 | T041 | Add approved Agent conversation, confirmation and weekly-report persistence schemas. |
| T005 | READY | T002, T003 | Seed four roles, permissions and reference data repeatably without migrating demos. |
| T006 | READY | T003 | Enforce PostgreSQL append-only audit chaining and a redacting audit service. |
| T007 | READY | T002 | Provide versioned secret encryption and SSRF-safe outbound endpoint validation. |
| T008 | DESIGN_GAP DG-02 | T002, T003, T041 | Build PostgreSQL-backed Celery dispatch, retry, lease and recovery infrastructure. |
| T009 | READY | T002, T003, T005, T006 | Migrate Cookie authentication, sessions, password change and lockout. |
| T010 | READY | T003, T005, T009 | Migrate permission enforcement and all five project data scopes. |
| T011 | READY | T005, T006, T010 | Migrate user administration and project assignment APIs. |
| T012 | READY | T005, T006, T010 | Migrate role, permission and department-option administration. |
| T013 | READY | T003, T006, T010 | Migrate versioned system configuration and project aliases. |
| T014 | READY | T003, T006, T007, T010 | Migrate AI Provider administration, connection tests, strategy and safe call logs. |
| T015 | READY | T006, T010 | Migrate audit query, integrity and redacted export APIs. |
| T016 | DESIGN_GAP DG-01 | T008, T011-T015, T018, T024 | Implement real management health, attention and audit overview data. |
| T017 | DESIGN_GAP DG-02 | T003, T006, T008, T010 | Move safe Excel upload and three-sheet preview parsing into durable workers. |
| T018 | READY | T017, T022 | Migrate atomic import commit, history, source download and supplemental matching through shared risk/todo services. |
| T019 | READY | T018 | Migrate safe import rollback and later-write conflict handling. |
| T020 | READY | T003, T010, T018 | Migrate scoped dashboard project, collection and risk read models. |
| T021 | READY | T003, T006, T010 | Migrate manager todo queries, updates and reusable risk-to-todo rules. |
| T022 | READY | T003, T006, T010, T021 | Migrate reusable risk mutation, two-state lifecycle and append-only timeline behavior. |
| T023 | READY | T002, T003, T006, T007, T010, T013 | Migrate encrypted per-user mailbox configuration and connection testing. |
| T024 | DESIGN_GAP DG-02/DG-10 | T008, T013, T023 | Implement durable scheduled/manual/retry UID synchronization. |
| T025 | DESIGN_GAP DG-10 | T007, T013, T024 | Parse mail and attachments safely and match standard projects/aliases. |
| T026 | Inherits DG-02/DG-10 | T006, T007, T008, T014, T022, T025 | Extract, review and transactionally publish mail risk candidates. |
| T027 | DESIGN_GAP DG-01/DG-09 | T004, T010, T020, T025, T026 | Build and expose authorized current-week report aggregates/details. |
| T028 | DESIGN_GAP DG-01 | T004, T010, T014, T020, T022, T026, T027 | Persist Agent conversations and expose authorized read-only business tools. |
| T029 | DESIGN_GAP DG-01/DG-03/DG-06/DG-07 | T004, T007, T008, T010, T014, T028 | Stream Agent text, progress, errors and mutation previews over SSE. |
| T030 | DESIGN_GAP DG-01/DG-03/DG-07 | T004, T006, T010, T021, T022, T029 | Execute previewed Agent writes through bound one-use REST confirmations. |
| T031 | Inherits DG-01/DG-03/DG-04/DG-06/DG-07/DG-09/DG-10 | T004, T006, T008, T013, T019, T024, T025, T042 | Run auditable import/conversation/temp retention cleanup with protections. |
| T032 | Inherits backend contract gaps | T040 | Freeze OpenAPI authority and generate reproducible frontend types. |
| T033 | Inherits backend contract gaps | T016, T032 | Cut admin pages to Python APIs and remove fixed business states. |
| T034 | DESIGN_GAP DG-01/DG-03/DG-06/DG-07 | T027, T030, T032 | Cut dashboard, weekly reports, mailbox and Agent UI to real Python APIs. |
| T035 | Inherits gaps | T031-T034, T040 | Define production Compose, Python processes, proxy, secrets and persistence after final backend/frontend composition. |
| T036 | DESIGN_GAP DG-04/DG-08 | T031, T035 | Implement encrypted backup rotation, isolated restore and drill verification. |
| T037 | Inherits DG-01-DG-04/DG-06-DG-10 | T033, T034, T036 | Prove full compatibility and security across the release candidate. |
| T038 | DESIGN_GAP DG-05 | T037 | Validate performance and resilience at the approved capacity baseline. |
| T039 | External inputs + gaps | T038 | Complete real mailbox/Provider E2E, restore evidence and Python-only cutover. |
| T040 | Inherits DG-01-DG-04/DG-06/DG-07/DG-09/DG-10 | T008-T031 | Compose all routers, dependencies, lifecycles and worker tasks once. |
| T041 | DESIGN_GAP DG-02 | T006 | Add the approved durable task/outbox persistence schema. |
| T042 | DESIGN_GAP DG-04 | T004, T013, T019 | Implement approved retention configuration and deletion-protection policy. |

## Design gaps

| ID | Missing approved decision | Affected tasks | Why ADRs do not answer it |
|---|---|---|---|
| DG-01 | Exact public request/response/error schemas, permission codes/default-role grants and endpoint set for dynamic admin overview, weekly report aggregate/detail, Agent conversation/query/preview/confirm/help. | T004, T016, T027-T035, T037, T039-T040 | ADR 0011 defines authority transition and ADR 0016 defines SSE properties, but neither defines concrete OpenAPI operations/event payloads or authorization grants; current TS contracts have none. |
| DG-02 | Durable task-state schema and ownership: task kinds/statuses, idempotency uniqueness, lease/heartbeat/reconciliation semantics and how domain batches reference it. | T008, T017, T024, T026, T031, T037-T041 | ADR 0006 requires PostgreSQL state and recovery but does not select the persistence contract needed for independent modules. |
| DG-03 | Confirmation-token lifetime and canonical content binding/replay contract; SSE reconnect/resume cursor semantics. | T004, T029-T035, T037, T039-T040 | ADRs 0013/0016 say short-lived, one-use and reconnect-capable but give no duration, canonical binding fields or resume behavior. |
| DG-04 | Configurable retention bounds, definition/storage of rollback window and audit hold, and deletion behavior for protected backup copies. | T031, T035-T037, T039-T040, T042 | ADR 0012 gives defaults and protections, not the state model or enforceable boundaries needed for deterministic deletion. |
| DG-05 | Numeric latency/throughput/failure-recovery acceptance thresholds at the approved capacity baseline. | T038, T039 | ADR 0009 specifies dataset size, RPO and RTO but no API/task performance PASS/FAIL thresholds. |
| DG-06 | Agent model-execution boundary: whether Agent AI invocation is an exception to “AI calls run in Celery”; if not, the durable event transport/order/cancellation/backpressure mechanism from Worker to SSE without making Redis a fact source. | T004, T029, T031-T035, T037, T039-T040 | Design §8 requires AI calls in Celery while §6/ADR 0016 requires live SSE and reconnect behavior; no approved source reconciles the process boundary. |
| DG-07 | Domain semantics for Agent “上报/处理”: required fields, mapping to risk/todo operations, duplicate/idempotency rule, allowed transitions and required timeline/audit linkage. | T004, T029-T035, T037, T039-T040 | The design names the operations and confirmation boundary but existing APIs only define todo updates and resolve/reopen; no source defines a safe manual-report/process transaction. |
| DG-08 | Backup encryption/key format and rotation interface plus the PostgreSQL/file consistency mechanism used to produce a verifiable restore set. | T036, T037, T039 | ADRs require encrypted backups and restoration of DB/files/config associations, but do not select a security/consistency mechanism an independent Agent can implement without a new architecture decision. |
| DG-09 | Weekly aggregate lifecycle: authoritative source rows, materialized-versus-query model, creation/rebuild/invalidation triggers, freshness guarantees and handling of late mail/candidate edits. | T004, T027-T028, T032, T034-T035, T037, T039-T040 | ADR 0015 requires a new weekly-aggregation table and the design defines the Shanghai week, but neither defines how that table stays consistent with mail and published risk state. |
| DG-10 | Mail fetch-to-parse/AI handoff: ownership and protection of transient raw content, crash/retry re-fetch behavior, stage completion contract and the exact point at which the mailbox UID cursor may advance. | T024-T027, T031-T035, T037, T039-T040 | ADRs 0006/0007 require recoverable tasks, minimal content retention, temp cleanup and cursor safety, but no source defines a cross-task handoff that satisfies all four constraints. |

## Integration checkpoints

1. Foundation: reproducible uv environment, app boot, PostgreSQL connectivity, migration/Seed from empty DB.
2. Security core: session/RBAC/scope/audit/secret negative tests.
3. Legacy parity: each migrated module passes old-contract comparison without NestJS production dependency.
4. Async workflows: broker loss, duplicate delivery, worker restart and temp cleanup tests.
5. Business closure: import and mail candidate transactions produce coherent risk/todo/timeline/audit state.
6. Contract authority: reviewed OpenAPI, generated frontend types and page E2E.
7. Release: security/capacity/recovery/external E2E evidence and Compose containing no NestJS service.
