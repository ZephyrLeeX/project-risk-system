from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from sqlalchemy import Enum
from sqlalchemy.dialects.postgresql import TIMESTAMP

from risk_platform.models import metadata

WORKSPACE = Path(__file__).resolve().parents[3]
PRISMA_SCHEMA = WORKSPACE / "apps/api/prisma/schema.prisma"
BASELINE_SQL = Path(__file__).resolve().parents[1] / (
    "alembic/versions/20260810_0001_core_schema.sql"
)


def _prisma_table_columns() -> dict[str, set[str]]:
    source = PRISMA_SCHEMA.read_text(encoding="utf-8")
    model_names = set(re.findall(r"^model (\w+) \{", source, flags=re.MULTILINE))
    result: dict[str, set[str]] = {}
    for model_name, body in re.findall(r"model (\w+) \{(.*?)\n\}", source, flags=re.DOTALL):
        mapped = re.search(r'@@map\("([^"]+)"\)', body)
        table_name = mapped.group(1) if mapped else model_name
        columns: set[str] = set()
        for line in body.splitlines():
            field = re.match(r"\s*(\w+)\s+(\w+)(\??|\[\])", line)
            if field and field.group(2) not in model_names and field.group(3) != "[]":
                columns.add(field.group(1))
        result[table_name] = columns
    return result


def test_metadata_has_final_prisma_tables_with_approved_audit_override() -> None:
    expected = _prisma_table_columns()
    expected["audit_logs"] = {
        "id",
        "actorUserId",
        "actorType",
        "module",
        "action",
        "resourceType",
        "resourceId",
        "result",
        "traceId",
        "requestId",
        "projectId",
        "failureCode",
        "previousHash",
        "integrityHash",
        "createdAt",
    }
    expected["import_batches"].add("taskId")
    expected["mail_sync_batches"].add("taskId")
    expected["mailbox_configs"].add("uidValidity")
    expected["mail_sync_batches"].update(
        {
            "uidValidity",
            "discoveredCount",
            "handedOffCount",
            "downstreamPendingCount",
            "retryableFailedCount",
            "permanentlyFailedCount",
            "cursorAdvanced",
        }
    )
    expected["durable_tasks"] = {
        "id",
        "kind",
        "status",
        "idempotencyKey",
        "payload",
        "attemptCount",
        "maxAttempts",
        "nextRetryAt",
        "leaseToken",
        "leaseOwner",
        "heartbeatAt",
        "leaseExpiresAt",
        "dispatchGeneration",
        "failureCode",
        "failureSummary",
        "startedAt",
        "completedAt",
        "createdAt",
        "updatedAt",
    }
    expected["task_outbox"] = {
        "id",
        "taskId",
        "dispatchGeneration",
        "publishedAt",
        "createdAt",
    }
    expected["agent_conversations"] = {
        "id",
        "ownerUserId",
        "createdAt",
        "updatedAt",
        "expiresAt",
        "lastMessageSequence",
        "lastEventSequence",
    }
    expected["agent_messages"] = {
        "id",
        "conversationId",
        "sequence",
        "role",
        "content",
        "traceId",
        "dataAsOf",
        "createdAt",
    }
    expected["agent_events"] = {
        "id",
        "conversationId",
        "messageId",
        "taskId",
        "sequence",
        "type",
        "payload",
        "createdAt",
    }
    expected["agent_confirmation_tokens"] = {
        "id",
        "tokenDigest",
        "ownerUserId",
        "conversationId",
        "operation",
        "canonicalContent",
        "contentDigest",
        "scopeDigest",
        "idempotencyKey",
        "issuedAt",
        "expiresAt",
        "usedAt",
        "resultResourceType",
        "resultResourceId",
    }
    expected["weekly_report_aggregates"] = {
        "id",
        "weekStart",
        "projectId",
        "summary",
        "riskCount",
        "riskLevelCounts",
        "sourceRevision",
        "stale",
        "generatedAt",
        "freshnessDeadline",
        "createdAt",
        "updatedAt",
    }
    expected["weekly_report_items"] = {
        "id",
        "aggregateId",
        "sourceMailId",
        "sourceCandidateId",
        "riskId",
        "todoId",
        "sourceRevision",
        "summary",
        "riskLevel",
        "riskStatus",
        "todoStatus",
        "occurredAt",
    }
    expected["mail_source_handoffs"] = {
        "id",
        "mailboxConfigId",
        "batchId",
        "parseTaskId",
        "uidValidity",
        "imapUid",
        "messageId",
        "envelopeMetadata",
        "fetchStatus",
        "handoffStatus",
        "parseStatus",
        "aiReviewStatus",
        "failureCode",
        "failureSummary",
        "createdAt",
        "updatedAt",
    }
    assert len(expected) == 37
    assert set(metadata.tables) == set(expected)
    for table_name, columns in expected.items():
        assert set(metadata.tables[table_name].columns.keys()) == columns


def test_postgresql_specific_types_are_preserved() -> None:
    assert str(metadata.tables["projects"].c.annualPlanAmount.type) == "NUMERIC(18, 2)"
    assert metadata.tables["projects"].c.monthlyCollections.type.__class__.__name__ == "JSONB"
    assert metadata.tables["mailbox_configs"].c.uidCursor.type.__class__.__name__ == "BigInteger"
    created_at = cast(TIMESTAMP, metadata.tables["users"].c.createdAt.type)
    assert created_at.timezone is True
    assert created_at.precision == 3
    risk_status = metadata.tables["risks"].c.status.type
    assert isinstance(risk_status, Enum)
    assert risk_status.enums == ["ACTIVE", "RESOLVED"]


def test_baseline_excludes_seed_and_t006_audit_enforcement() -> None:
    sql = BASELINE_SQL.read_text(encoding="utf-8")
    forbidden = ("INSERT INTO", "CREATE TRIGGER", "CREATE EXTENSION", "audit_log_compute_hash")
    assert all(token not in sql for token in forbidden)
    assert 'ADD COLUMN "integrityHash" VARCHAR(64)' in sql
    assert 'CREATE TABLE "mail_risk_candidates"' in sql


def test_no_runtime_create_all() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    for path in source_root.rglob("*.py"):
        assert ".create_all(" not in path.read_text(encoding="utf-8")


def test_prisma_python_side_defaults_are_complete_without_ddl_drift() -> None:
    uuid_default_columns = [
        table.c.id
        for table in metadata.tables.values()
        if "id" in table.c and len(table.primary_key.columns) == 1
    ]
    assert len(uuid_default_columns) == 34
    assert all(column.default is not None for column in uuid_default_columns)
    assert all(column.server_default is None for column in uuid_default_columns)

    updated_at_columns = [
        table.c.updatedAt for table in metadata.tables.values() if "updatedAt" in table.c
    ]
    assert len(updated_at_columns) == 21
    assert all(column.default is not None for column in updated_at_columns)
    assert all(column.onupdate is not None for column in updated_at_columns)
    assert all(column.server_default is None for column in updated_at_columns)
