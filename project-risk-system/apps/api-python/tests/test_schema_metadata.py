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


def test_metadata_has_exact_final_prisma_tables_and_columns() -> None:
    expected = _prisma_table_columns()
    assert len(expected) == 28
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
