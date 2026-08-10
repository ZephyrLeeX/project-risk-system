# Global Implementation Constraints

These constraints apply to every Txxx task and are part of each task's acceptance criteria.

## Scope discipline

1. Implement only the assigned objective and its required compatibility tests. Do not “prepare” unrelated later features.
2. Respect the declared read/write set. Expansion requires recording why it is necessary; stop if it changes another module's public contract or architecture.
3. Do not modify NestJS behavior, Prisma schema, transitional TypeScript contracts or frontend unless the task explicitly authorizes it.
4. Do not resolve a `DESIGN_GAP` by choosing a contract, table ownership rule, retention meaning, performance SLO or security mechanism not already approved.

## Architecture and data

- Target code lives under `project-risk-system/apps/api-python/`; keep modules explicit and prohibit ad-hoc cross-module table access.
- PostgreSQL only, including tests that assert database behavior. Never introduce SQLite.
- All DDL is Alembic-managed; startup never creates production tables. Migrations and Seed must be repeatable from an empty database.
- Preserve Prisma-equivalent core fields, enums, UUIDs, constraints, timestamps and deletion behavior. Additive deviations require the relevant approved source.
- Business writes that span risk, todo, timeline, audit or candidate state are one transaction.
- Redis loss/duplication cannot lose or duplicate business facts. Durable task state, idempotency and recovery live in PostgreSQL.

## Contract and security

- Maintain `/api`, response envelope, trace ID, status/error, pagination and Cookie semantics until OpenAPI authority is explicitly transitioned.
- Validate request and untrusted output strictly; reject unknown fields where legacy behavior does so. Do not leak secret, mail body/attachment text, prompt or raw model response through response/log/audit/error.
- Apply both permission and current project-scope filtering to lists, counts, statistics, details, exports and Agent tools. Include negative tests.
- Store UTC; calculate “本周周报” in `Asia/Shanghai`, Monday `[00:00, next Monday 00:00)`, preferring sent time over received time.
- Secrets come from Docker Secret/read-only files in production; encrypted records carry a key version. Outbound IMAP/provider destinations undergo approved SSRF/DNS/IP validation.
- Audit-sensitive success and failure paths. PostgreSQL must prevent audit update/delete and verify the hash chain; never auto-repair a break.

## Reliability and retention

- Tasks require idempotency keys, explicit timeouts, finite retries/backoff, concurrency limits, safe failure reasons and restart reconciliation.
- One mailbox has at most one active sync; failed mail does not advance its UID cursor. Always clean temporary files.
- Retain import source files one year and Agent full conversations 90 days by default; do not delete protected or rollback-eligible artifacts. Business/audit results survive source cleanup.
- Capacity baseline: 300 users, 5,000 projects, 1,000 weekly mails. Numeric performance PASS/FAIL awaits the recorded design gap resolution.

## Quality and completion

Each task must add proportionate unit tests and PostgreSQL integration/contract/negative tests named by the task. Run the task commands plus, where available:

```bash
cd project-risk-system/apps/api-python
uv run ruff check .
uv run mypy .
uv run pytest
```

Do not claim success when a required external mailbox/provider/TLS/backup destination is unavailable; report the blocked validation separately. A task passes only when every acceptance item and deliverable is present, no unrelated writes exist, and documentation/OpenAPI/migrations are consistent.

## Stop conditions common to all tasks

Stop and report instead of coding when sources conflict on business semantics; an undocumented breaking contract or destructive migration is required; secrets or real external credentials would be committed/exposed; the write set materially overlaps an in-progress task; a named `DESIGN_GAP` is unresolved; or validation would require fabricating external success.
