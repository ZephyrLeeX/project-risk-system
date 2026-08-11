# Implementation Baseline

## Approval and authority

This file records the implementation baseline approved by the user on 2026-08-10. Although `docs/fastapi-backend-design.md` still contains the text “状态：待最终确认”, the user directive that created this task graph approves that document as the implementation source of truth. An implementation Agent must not reopen approved architecture through inference.

Conflict order is: confirmed user decision and ADR; `docs/fastapi-backend-design.md`; current frontend-visible behavior and `project-risk-system/packages/contracts/src/index.ts`; current NestJS/Prisma behavior and tests as explanatory compatibility evidence; older specifications and prototypes. A business-semantic conflict stops the task.

Every task Agent must read, before editing: repository `AGENTS.md` if present; this file; `GLOBAL_CONSTRAINTS.md`; its assigned task; every source and ADR named by that task. No chat history is authoritative.

## Repository baseline

- Product workspace: `project-risk-system/`; target Python service location: `project-risk-system/apps/api-python/`.
- Existing frontend: Vue 3/Vite in `apps/web`; its `/api` calls and visible states are compatibility inputs.
- Transitional contract: `packages/contracts/src/index.ts` (1,216 lines at graph creation). It contains existing auth, admin, provider, config, import, dashboard, risk, todo, audit and mailbox types, but no approved Agent/weekly-report contract.
- Legacy reference API: NestJS in `apps/api/src`; 13 Prisma migrations and `apps/api/prisma/schema.prisma` define 24 mature tables/models through `MailRiskCandidate`.
- Existing behavior covers auth/session, users/roles/scopes, project imports, dashboard/collections, risks/todos/timeline, AI provider administration, system config, audit APIs, mailbox configuration and mailbox result/review APIs. Mail processing is request-process based, not the approved Celery architecture.
- Known non-target behavior: Dashboard Agent replies are local keyword responses; Admin Dashboard has fixed health/attention/audit content; the weekly report dashboard section is absent/incomplete; current Compose starts only PostgreSQL; current health check is static.
- Existing source files remain read-only references unless a task explicitly includes them in its write set. Production must ultimately exclude NestJS.

## Approved architecture baseline

- Python 3.12 modular FastAPI monolith; Pydantic v2, SQLAlchemy 2, Alembic, psycopg 3; uv lock; pytest, Ruff and mypy gates.
- PostgreSQL is the sole formal database. API and independent Celery workers share domain services. Redis is a broker only; PostgreSQL owns business/task state.
- Preserve mature schema semantics and API-observable behavior; add tables only for approved new capabilities. UTC persistence and `Asia/Shanghai` business display.
- Cookie sessions, forced first-password change, login lockout; four seeded roles and five project data scopes; permission and data-scope checks are separate and universal.
- Risks have only `ACTIVE` and `RESOLVED`; progress belongs to todos/timeline.
- Excel and mail/AI work is durable, idempotent, bounded, retryable and recoverable after restart.
- Audit is PostgreSQL-enforced append-only hash chaining with a fixed typed metadata-only write interface; it stores no snapshot or arbitrary payload and has no redaction subsystem. Secrets are versioned encrypted values; outbound endpoints and untrusted files/model output are constrained.
- Agent uses authorized business tools only, SSE for text/progress/preview, and separate REST confirmation with a single-use short-lived token. Streaming never writes business mutations.
- Single internal server, HTTPS reverse proxy, Docker Compose, persistent volumes, encrypted daily backups, specified retention, RPO 24h/RTO 4h.

## Compatibility surfaces

- `/api` paths, fields, envelopes (`code`, `message`, `data`, `traceId`), status/error semantics, Cookie behavior and pagination remain compatible during migration.
- Core database enums/constraints/relations must be checked against `schema.prisma` and migrations, not guessed from names.
- Frontend visual redesign is out of scope; generated OpenAPI types replace handwritten authority only after backend cutover readiness.
- Mocks may isolate external systems in automated tests but never prove business acceptance.

## Baseline changes

Changes to approved architecture, business terminology, core lifecycle, scope semantics, retention policy, security boundary, deployment topology or public contracts require a new/updated ADR or explicit design addendum. Implementation discoveries are recorded as `DESIGN_GAP`; they are not silently resolved in code.

## Task graph audit provenance

- Audited implementation task graph commit: `fb83b05925d7cbae2421ac229b1b58e0759eb028` (`docs: audit implementation task graph`).
- The SHA is recorded in the immediately following metadata commit because a Git commit cannot contain its own final SHA.
