# Encrypted Backup & Restore Runbook (T036 / ADR 0031)

One-shot, quiesce-coordinated, AES-256-GCM envelope-encrypted backup and
fail-closed isolated restore for the project-risk-system. Backup authority =
PostgreSQL (full schema/data/audit chain) + durable file storage
(`project-risk-storage`); Redis/Celery/cache/temp/logs are excluded (ADR 0031 §1).

This package owns only `infra/backup/**`. It reuses — never edits — the frozen
application composition, Compose topology (T035) and `risk_platform.shared.crypto`
(T007 `rpenc` / `KeyRing`). Backup/restore are **one-shot commands**, not a
Compose service; no change to `infra/docker-compose.yml`.

## Layout

```
infra/backup/
  src/risk_backup/   implementation (manifest, envelope, archive, pgdump,
                    quiesce, reconcile, backup, restore, cli)
  tests/             focused backup/restore + negative tests (real PostgreSQL 16)
  pyproject.toml     tool config only (ruff/mypy/pytest); not an installable project
```

## Backup set & consistency

A backup is a single **consistent set** captured under quiesce (ADR 0031 §2):

1. **Quiesce** — stop the three write paths (`api`, `worker`, `scheduler`).
   PostgreSQL + Redis stay up for capture. The API is stopped (not placed in a
   read-allowed maintenance mode) because adding such a mode is outside T036's
   frozen write-set; a full stop is a strictly stronger quiesce that still
   guarantees the snapshot/file alignment invariant. Quiesce confirmation
   failure is fail-closed (no usable backup).
2. **PostgreSQL capture** — `pg_dump -Fc` (single logical snapshot; no
   WAL/PITR — out of scope). `pg_dump`/`pg_restore` are provided by the
   `postgres:16-alpine` image (ADR 0031 §12).
3. **File capture** — tar of the durable `project-risk-storage` volume
   (`/app/storage`), excluding temp/scratch subdirs. Because writes are
   quiesced, every file the DB snapshot references is on disk and no new
   durable file can appear in the window.
4. **Manifest** — versioned JSON binding both components (names, sizes, sha256,
   alembic head, KEK key version). Encrypted as the first payload component; its
   canonical bytes are the AEAD associated data for every other component.
5. **Encrypt** — AES-256-GCM envelope: random per-backup DEK wraps the payload;
   the DEK is wrapped by the active backup KEK version via `rpenc`
   (`risk_platform.shared.crypto`). Artifact stores only the wrapped DEK + KEK
   key version — never the KEK or DEK itself.
6. **Cleanup** — plaintext temps (pgdump, tar) are overwritten+unlinked on
   success and failure. A cleanup failure does not invalidate the encrypted
   artifact but raises a **SEVERE** alert (`PLAINTEXT_CLEANUP_FAILED`) — scrub
   the temp dir before trusting the backup.
7. **Unquiesce** — restart the stack. Unquiesce failure leaves a usable artifact
   but is surfaced (restart manually).

Three integrity layers (ADR 0031 §4): DEK-wrap GCM tag, every payload chunk's
GCM tag, and manifest component sha256s.

## Restore (fail-closed, isolated target)

Restore never touches the live system by default. Target = an isolated **empty**
database + empty storage directory. Sequence (ADR 0031 §8):

KEK lookup → DEK unwrap (AEAD) → payload decrypt (AEAD) → manifest parse/validate
→ component sha256 verify → `pg_restore` into empty DB → audit hash-chain verify
(ADR 0008) → alembic-head match → file extract → orphan/missing reconcile. Any
failure aborts with no partial success. A mismatched/partial set, a broken audit
chain, a missing referenced file, or a tampered component all fail closed.

## Key management

The backup KEK is a 256-bit key, **independent** from `DATA_ENCRYPTION_KEY`,
loaded from host read-only files (`KeyRing.from_files` model — never from env).
New backups use the active KEK version; historical backups decrypt via retained
versions with **no re-encryption** (ADR 0031 §6). Retire a KEK version only after
all backups it protected are outside the 7/4/12 retention window, hold-free
(ADR 0027), and unreferenced by an unfinished drill.

Generate a backup KEK (host only, gitignored — e.g. `/etc/risk/backup-keys/`):

```sh
openssl rand -base64 32 > /etc/risk/backup-keys/backup_kek_v1
chmod 0400 /etc/risk/backup-keys/backup_kek_v1
```

## Production invocation

The command runs in the `risk-platform-api:0.1.0` image (provides `cryptography`
+ `risk_platform.shared.crypto` + this package mounted at runtime); `pg_dump` /
`pg_restore` run inside the `postgres:16-alpine` container via the `--pg-runner`
prefix. KEK and output are mounted; nothing is committed.

```sh
# 1. Quiesce write paths (postgres + redis stay up).
docker compose --env-file .env.production -f infra/docker-compose.yml stop api worker scheduler

# 2. Backup (api image runs the orchestrator; pg_dump runs in the postgres container).
docker compose --env-file .env.production -f infra/docker-compose.yml run --rm --no-deps \
  -v project-risk-storage:/app/storage:ro \
  -v /var/backups/risk:/backup \
  -v /etc/risk/backup-keys:/keys:ro \
  -v "$(pwd)/infra/backup/src:/opt/risk_backup:ro" \
  -e PYTHONPATH=/opt/risk_backup \
  api python -m risk_backup backup \
    --type daily \
    --dsn "postgresql://project_risk:${POSTGRES_PASSWORD}@postgres:5432/project_risk" \
    --pg-runner "docker exec -i project-risk-postgres" \
    --pg-socket-dir /var/run/postgresql \
    --pg-user project_risk --pg-db project_risk \
    --storage-root /app/storage \
    --output /backup/daily.rpbk \
    --temp-dir /tmp/risk-backup \
    --kek-version v1 --kek-file v1=/keys/backup_kek_v1 \
    --quiesce none   # quiesce already performed above

# 3. Unquiesce.
docker compose --env-file .env.production -f infra/docker-compose.yml up -d --no-deps api worker scheduler
```

> `--quiesce none` is used here because the host script performs the stop/start
> around the one-shot container. For a single in-process job with the docker
> socket available, use `--quiesce compose` instead and let the orchestrator
> own the full quiesce/unquiesce cycle.

Isolated restore drill (ADR 0009 — a backup is valid only after a real restore):

```sh
# Create an isolated empty target DB + empty storage dir first.
docker exec project-risk-postgres psql -U project_risk -d project_risk -c 'CREATE DATABASE restore_drill'
mkdir -p /var/tmp/restore-drill/storage && rm -rf /var/tmp/restore-drill/storage/*

docker compose --env-file .env.production -f infra/docker-compose.yml run --rm --no-deps \
  -v /var/backups/risk:/backup:ro \
  -v /var/tmp/restore-drill:/drill \
  -v /etc/risk/backup-keys:/keys:ro \
  -v "$(pwd)/infra/backup/src:/opt/risk_backup:ro" \
  -e PYTHONPATH=/opt/risk_backup \
  api python -m risk_backup restore \
    --artifact /backup/daily.rpbk \
    --target-dsn "postgresql://project_risk:${POSTGRES_PASSWORD}@postgres:5432/restore_drill" \
    --pg-runner "docker exec -i project-risk-postgres" \
    --pg-socket-dir /var/run/postgresql \
    --pg-user project_risk --pg-db restore_drill \
    --target-storage-root /drill/storage \
    --temp-dir /tmp/risk-restore \
    --kek-version v1 --kek-file v1=/keys/backup_kek_v1
```

The drill verifies DB/files/config associations + the audit hash chain and
records the measured RTO. Drop the drill DB afterwards.

## Retention

ADR 0009 retention (7 daily / 4 weekly / 12 monthly) is driven by host cron or
manual operations — **not** by this package. Deletion of a backup copy is gated
by the ADR 0027 `BACKUP_COPY` predicate (outside the retention set, no active
hold, no unfinished drill) and writes `BACKUP_COPY_DELETED` audit; `backupId` is
the stable `BACKUP_COPY` identifier.

## Logging boundary

Only metadata is logged (ADR 0031 §10 / ADR 0027): backupId, type, time, KEK key
**version** (never the key), component names/sizes/sha256, status, RTO, error
codes. Backup creation, restore and KEK retirement are operational events logged
to the operator log stream — **not** written to the PostgreSQL business audit
chain (the backup job constructs no domain service and writes no business audit,
ADR 0017). Only ADR 0027's approved `BACKUP_COPY_DELETED` (copy deletion) enters
the business audit chain.
