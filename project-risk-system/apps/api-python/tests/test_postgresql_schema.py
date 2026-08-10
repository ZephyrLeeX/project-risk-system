from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Enum, create_engine, inspect, text
from sqlalchemy.dialects.postgresql.base import PGInspector
from sqlalchemy.schema import PrimaryKeyConstraint

from risk_platform.db import (
    create_database_engine,
    create_session_factory,
    dispose_database_engine,
    transaction,
)
from risk_platform.models import metadata

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated_postgresql_schema() -> Iterator[Connection]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL runtime validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t003_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as connection:
            config = Config(ROOT / "alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
            yield connection
            command.check(config)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_upgrade_creates_equivalent_tables_columns_indexes_and_foreign_keys(
    migrated_postgresql_schema: Connection,
) -> None:
    inspector = inspect(migrated_postgresql_schema)
    assert set(inspector.get_table_names()) == set(metadata.tables) | {"alembic_version"}
    for table_name, table in metadata.tables.items():
        actual_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert set(actual_columns) == set(table.columns.keys())
        for column in table.columns:
            actual = actual_columns[column.name]
            assert actual["nullable"] is column.nullable
            assert actual["type"].compile(dialect=migrated_postgresql_schema.dialect) == (
                column.type.compile(dialect=migrated_postgresql_schema.dialect)
            )

        actual_pk = inspector.get_pk_constraint(table_name)
        expected_pk = next(
            constraint
            for constraint in table.constraints
            if isinstance(constraint, PrimaryKeyConstraint)
        )
        assert actual_pk["name"] == expected_pk.name
        assert actual_pk["constrained_columns"] == [column.name for column in expected_pk.columns]

        actual_indexes = {
            index["name"]: (index["column_names"], index["unique"])
            for index in inspector.get_indexes(table_name)
        }
        expected_indexes = {
            index.name: (
                [getattr(expression, "name", None) for expression in index.expressions],
                index.unique,
            )
            for index in table.indexes
        }
        assert actual_indexes.items() >= expected_indexes.items()

        actual_fks = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys(table_name)
            if foreign_key["name"] is not None
        }
        expected_fks = list(table.foreign_key_constraints)
        assert set(actual_fks) == {constraint.name for constraint in expected_fks}
        for constraint in expected_fks:
            assert isinstance(constraint.name, str)
            options = actual_fks[constraint.name]["options"]
            assert options.get("ondelete") == constraint.ondelete
            assert options.get("onupdate") == constraint.onupdate

    mailbox_checks = {item["name"] for item in inspector.get_check_constraints("mailbox_configs")}
    assert mailbox_checks == {"mailbox_configs_port_check", "mailbox_configs_weeks_check"}


def test_enum_values_and_single_alembic_head(
    migrated_postgresql_schema: Connection,
) -> None:
    pg_inspector = cast(PGInspector, inspect(migrated_postgresql_schema))
    actual_enums = {item["name"]: item["labels"] for item in pg_inspector.get_enums()}
    expected_enums = {
        column.type.name: list(column.type.enums)
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Enum)
    }
    assert actual_enums == expected_enums
    config = Config(ROOT / "alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["20260810_0001"]


def test_baseline_downgrade_policy_is_non_destructive(
    migrated_postgresql_schema: Connection,
) -> None:
    config = Config(ROOT / "alembic.ini")
    config.attributes["connection"] = migrated_postgresql_schema
    with pytest.raises(NotImplementedError, match="不提供破坏性 downgrade"):
        command.downgrade(config, "base")
    migrated_postgresql_schema.rollback()
    assert "users" in inspect(migrated_postgresql_schema).get_table_names()


def test_audit_enforcement_is_explicitly_deferred_to_t006(
    migrated_postgresql_schema: Connection,
) -> None:
    triggers = migrated_postgresql_schema.execute(
        text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
    ).scalars()
    assert list(triggers) == []


def test_request_and_worker_transactions_commit_rollback_and_dispose(
    migrated_postgresql_schema: Connection,
) -> None:
    schema = migrated_postgresql_schema.scalar(text("SELECT current_schema()"))
    assert isinstance(schema, str)
    migrated_postgresql_schema.execute(
        text("CREATE TABLE t003_transaction_probe (id INTEGER PRIMARY KEY)")
    )
    migrated_postgresql_schema.commit()
    url = os.environ["TEST_DATABASE_URL"]
    async_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)

    async def exercise() -> None:
        # The URL is used exclusively by this ephemeral test schema.
        scoped_engine = create_database_engine(
            f"{async_url}?options=-csearch_path%3D{schema}", pool_pre_ping=False
        )
        scoped_factory = create_session_factory(scoped_engine)
        async with transaction(scoped_factory) as session:
            await session.execute(text("INSERT INTO t003_transaction_probe VALUES (1)"))
        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(scoped_factory) as session:
                await session.execute(text("INSERT INTO t003_transaction_probe VALUES (2)"))
                raise RuntimeError("rollback")
        await dispose_database_engine(scoped_engine)

    try:
        asyncio.run(exercise())
        count = migrated_postgresql_schema.scalar(
            text("SELECT count(*) FROM t003_transaction_probe")
        )
        assert count == 1
    finally:
        migrated_postgresql_schema.rollback()
        migrated_postgresql_schema.execute(text("DROP TABLE IF EXISTS t003_transaction_probe"))
        migrated_postgresql_schema.commit()
