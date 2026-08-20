"""Synchronous PostgreSQL probes for backup/restore (ADR 0031 §3, §8).

All queries are metadata-only: they read schema/audit/retention facts needed to
build and verify a backup set. They never return or log file contents, dump
contents, mail content or credentials. The audit-chain verification reuses the
approved ``audit_log_compute_hash`` function and trigger-enforced append-only
chain (ADR 0008) restored verbatim from the ``pg_dump``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from risk_backup.errors import BackupError

_AUDIT_VERIFY_SQL = """
WITH ordered AS (
  SELECT
    log_entry.*,
    lag("integrityHash") OVER (ORDER BY "createdAt", "id") AS expected_previous_hash,
    row_number() OVER (ORDER BY "createdAt", "id") AS position
  FROM "audit_logs" AS log_entry
), checked AS (
  SELECT
    "id", position,
    (
      "previousHash" IS NOT DISTINCT FROM expected_previous_hash
      AND "integrityHash" = audit_log_compute_hash(
        "id", "actorUserId", "actorType"::text, "module", "action",
        "resourceType", "resourceId", "result"::text, "traceId", "requestId",
        "projectId", "failureCode", "previousHash", "createdAt"
      )
    ) AS valid
  FROM ordered
)
SELECT
  count(*)::bigint AS total_records,
  count(*) FILTER (WHERE valid)::bigint AS verified_records,
  (array_agg("id" ORDER BY position) FILTER (WHERE NOT valid))[1] AS first_broken_event_id
FROM checked
"""


@dataclass(frozen=True, slots=True)
class AuditChainStatus:
    valid: bool
    total_records: int
    verified_records: int


def connect(dsn: str) -> psycopg.Connection[Any]:
    try:
        return psycopg.connect(dsn)
    except psycopg.Error as exc:
        raise BackupError("DB_CONNECT_FAILED", cause=exc) from exc


def server_version(conn: psycopg.Connection[Any]) -> str:
    with conn.cursor() as cur:
        cur.execute("SHOW server_version")
        row = cur.fetchone()
    if row is None:
        raise BackupError("DB_VERSION_UNAVAILABLE")
    return str(row[0])


def alembic_head(conn: psycopg.Connection[Any]) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version_num FROM "alembic_version"')
            row = cur.fetchone()
    except psycopg.Error as exc:
        raise BackupError("ALEMBIC_HEAD_UNAVAILABLE", cause=exc) from exc
    if row is None:
        raise BackupError("ALEMBIC_HEAD_UNAVAILABLE")
    return str(row[0])


def schema_hash(conn: psycopg.Connection[Any]) -> str:
    """sha256 over the sorted set of public-schema table names.

    A coarse schema fingerprint recorded in the manifest (optional per ADR 0031
    §3 ``schemaHash``); restore compares it to detect schema drift.
    """

    import hashlib

    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        names = [str(row[0]) for row in cur.fetchall()]
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()


def audit_chain_status(conn: psycopg.Connection[Any]) -> AuditChainStatus:
    try:
        with conn.cursor() as cur:
            cur.execute(_AUDIT_VERIFY_SQL)
            row = cur.fetchone()
    except psycopg.Error as exc:
        raise BackupError("AUDIT_CHAIN_QUERY_FAILED", cause=exc) from exc
    if row is None:
        raise BackupError("AUDIT_CHAIN_QUERY_FAILED")
    total, verified, broken = int(row[0]), int(row[1]), row[2]
    return AuditChainStatus(valid=broken is None, total_records=total, verified_records=verified)


def import_storage_keys(conn: psycopg.Connection[Any]) -> list[str]:
    """Durable file references recorded in the DB (import source workbooks).

    Used by restore reconcile (ADR 0031 §2/§8): every referenced file must exist
    in the restored file set; unreferenced files are orphans to discard.
    """

    try:
        with conn.cursor() as cur:
            cur.execute('SELECT "storageKey" FROM "import_batches"')
            return [str(row[0]) for row in cur.fetchall()]
    except psycopg.Error as exc:
        raise BackupError("FILE_REFERENCE_QUERY_FAILED", cause=exc) from exc


def assert_database_empty(conn: psycopg.Connection[Any]) -> None:
    """Fail-closed unless the target database contains no application tables.

    Restore defaults to an isolated empty target (ADR 0031 §8); a non-empty
    target risks blending a partial restore with live data.
    """

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
        row = cur.fetchone()
    count = int(row[0]) if row else 0
    if count != 0:
        raise BackupError("RESTORE_TARGET_NOT_EMPTY")


__all__ = [
    "AuditChainStatus",
    "alembic_head",
    "assert_database_empty",
    "audit_chain_status",
    "connect",
    "import_storage_keys",
    "schema_hash",
    "server_version",
]
