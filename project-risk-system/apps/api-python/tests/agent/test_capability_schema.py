from __future__ import annotations

import ast
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from risk_platform.models import metadata
from risk_platform.reliability.models import DurableTaskKind

ROOT = Path(__file__).resolve().parents[2]
T004_REVISION = ROOT / "alembic" / "versions" / "20260811_0004_agent_weekly_capabilities.py"


@pytest.fixture
def capability_schema() -> Iterator[Connection]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL capability-schema validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t004_{uuid.uuid4().hex}"
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


def test_metadata_has_approved_agent_and_weekly_capability_tables() -> None:
    assert DurableTaskKind.WEEKLY_REPORT_REBUILD.value == "WEEKLY_REPORT_REBUILD"
    expected_tables = {
        "agent_conversations",
        "agent_messages",
        "agent_events",
        "agent_confirmation_tokens",
        "weekly_report_aggregates",
        "weekly_report_items",
    }
    assert expected_tables.issubset(metadata.tables)

    event_task = metadata.tables["agent_events"].c.taskId
    assert event_task.nullable is False
    assert next(iter(event_task.foreign_keys)).target_fullname == "durable_tasks.id"
    assert next(iter(event_task.foreign_keys)).ondelete == "RESTRICT"

    item = metadata.tables["weekly_report_items"]
    assert {foreign_key.target_fullname for foreign_key in item.foreign_keys} == {
        "weekly_report_aggregates.id",
        "mail_messages.id",
        "mail_risk_candidates.id",
        "risks.id",
        "action_items.id",
    }
    assert all(foreign_key.ondelete == "RESTRICT" for foreign_key in item.foreign_keys)


def test_t004_named_constraints_and_indexes_fit_postgresql_identifier_limit() -> None:
    source = T004_REVISION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: list[str] = []

    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr == "create_index":
            index_name = call.args[0]
            assert isinstance(index_name, ast.Constant) and isinstance(index_name.value, str)
            names.append(index_name.value)
            continue
        if call.func.attr != "create_table":
            continue

        table_name = call.args[0]
        assert isinstance(table_name, ast.Constant) and isinstance(table_name.value, str)
        for constraint in call.args[1:]:
            if not isinstance(constraint, ast.Call) or not isinstance(
                constraint.func, ast.Attribute
            ):
                continue
            if constraint.func.attr not in {
                "CheckConstraint",
                "ForeignKeyConstraint",
                "PrimaryKeyConstraint",
            }:
                continue
            name = next(
                (
                    keyword.value.value
                    for keyword in constraint.keywords
                    if keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            assert isinstance(name, str)
            names.append(
                f"{table_name.value}_{name}"
                if constraint.func.attr == "CheckConstraint"
                else name
            )

    assert names
    assert all(len(name.encode("utf-8")) <= 63 for name in names)
    assert "weekly_report_items_aggregate_sources_key" in names


def test_latest_upgrade_has_one_head_and_capability_constraints(
    capability_schema: Connection,
) -> None:
    config = Config(ROOT / "alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["20260811_0005"]
    inspector = inspect(capability_schema)
    assert {
        "agent_conversations",
        "agent_messages",
        "agent_events",
        "agent_confirmation_tokens",
        "weekly_report_aggregates",
        "weekly_report_items",
    }.issubset(inspector.get_table_names())
    assert {
        item["name"] for item in inspector.get_check_constraints("agent_confirmation_tokens")
    } == {
        "agent_confirmation_tokens_canonical_content_nonempty",
        "agent_confirmation_tokens_expiry_after_issue",
        "agent_confirmation_tokens_idempotency_key_nonempty",
        "agent_confirmation_tokens_result_resource_pair",
    }
    assert {
        item["name"] for item in inspector.get_check_constraints("weekly_report_aggregates")
    } == {
        "weekly_report_aggregates_freshness_after_generated",
        "weekly_report_aggregates_risk_count_nonnegative",
        "weekly_report_aggregates_risk_level_counts_object",
        "weekly_report_aggregates_source_revision_positive",
        "weekly_report_aggregates_summary_object",
        "weekly_report_aggregates_week_start_is_monday",
    }


def test_database_enforces_sequences_confirmation_uniqueness_and_week_start(
    capability_schema: Connection,
) -> None:
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    message_id = uuid.uuid4()
    capability_schema.execute(
        text(
            'INSERT INTO users (id, username, "passwordHash", "displayName", "updatedAt") '
            "VALUES (:id, :username, :password_hash, 'T004', CURRENT_TIMESTAMP)"
        ),
        {"id": user_id, "username": f"t004-{user_id}", "password_hash": "not-a-real-password-hash"},
    )
    capability_schema.execute(
        text(
            'INSERT INTO durable_tasks (id, kind, "idempotencyKey", payload, "maxAttempts", '
            '"updatedAt") VALUES (:id, CAST(\'IMPORT_PREVIEW\' AS "DurableTaskKind"), '
            ":key, '{}'::jsonb, 1, CURRENT_TIMESTAMP)"
        ),
        {"id": task_id, "key": f"t004:{task_id}"},
    )
    capability_schema.execute(
        text(
            'INSERT INTO agent_conversations (id, "ownerUserId", "expiresAt", "updatedAt") '
            "VALUES (:id, :owner_id, CURRENT_TIMESTAMP + interval '90 days', CURRENT_TIMESTAMP)"
        ),
        {"id": conversation_id, "owner_id": user_id},
    )
    capability_schema.execute(
        text(
            'INSERT INTO agent_messages (id, "conversationId", sequence, role, content, "traceId") '
            "VALUES (:id, :conversation_id, 1, "
            "CAST('USER' AS \"AgentMessageRole\"), 'hello', 't004')"
        ),
        {"id": message_id, "conversation_id": conversation_id},
    )
    capability_schema.execute(
        text(
            "INSERT INTO agent_events "
            '(id, "conversationId", "messageId", "taskId", sequence, type, payload) '
            "VALUES (:id, :conversation_id, :message_id, :task_id, 1, "
            "CAST('progress' AS \"AgentEventType\"), '{}'::jsonb)"
        ),
        {
            "id": uuid.uuid4(),
            "conversation_id": conversation_id,
            "message_id": message_id,
            "task_id": task_id,
        },
    )
    capability_schema.commit()

    with pytest.raises(DBAPIError), capability_schema.begin_nested():
        capability_schema.execute(
            text(
                "INSERT INTO agent_messages "
                '(id, "conversationId", sequence, role, content, "traceId") '
                "VALUES (:id, :conversation_id, 3, "
                "CAST('USER' AS \"AgentMessageRole\"), 'gap', 't004')"
            ),
            {"id": uuid.uuid4(), "conversation_id": conversation_id},
        )

    confirmation_sql = text(
        "INSERT INTO agent_confirmation_tokens "
        '(id, "tokenDigest", "ownerUserId", "conversationId", operation, '
        '"canonicalContent", "contentDigest", "scopeDigest", "idempotencyKey", '
        '"issuedAt", "expiresAt") '
        "VALUES (:id, :digest, :owner_id, :conversation_id, "
        "CAST('REPORT' AS \"AgentConfirmationOperation\"), '{}', :digest, :digest, :key, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + interval '10 minutes')"
    )
    confirmation_values = {
        "digest": "a" * 64,
        "owner_id": user_id,
        "conversation_id": conversation_id,
        "key": "t004:confirmation",
    }
    capability_schema.execute(confirmation_sql, {"id": uuid.uuid4(), **confirmation_values})
    capability_schema.commit()

    with pytest.raises(IntegrityError), capability_schema.begin_nested():
        capability_schema.execute(confirmation_sql, {"id": uuid.uuid4(), **confirmation_values})
