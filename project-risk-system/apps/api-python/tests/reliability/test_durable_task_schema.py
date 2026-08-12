from __future__ import annotations

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
from sqlalchemy.exc import IntegrityError

from risk_platform.models import metadata
from risk_platform.reliability.models import DurableTaskKind, DurableTaskStatus

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def durable_task_schema() -> Iterator[Connection]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL durable-task validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t041_{uuid.uuid4().hex}"
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


def _insert_task(
    connection: Connection,
    *,
    task_id: uuid.UUID | None = None,
    kind: str = "IMPORT_PREVIEW",
    idempotency_key: str = "import:batch-1",
    status: str = "QUEUED",
    next_retry_at: str | None = None,
    completed_at: str | None = None,
) -> uuid.UUID:
    identifier = task_id or uuid.uuid4()
    connection.execute(
        text(
            'INSERT INTO durable_tasks '
            '(id, kind, status, "idempotencyKey", payload, "attemptCount", "maxAttempts", '
            '"nextRetryAt", "dispatchGeneration", "completedAt", "updatedAt") '
            "VALUES (:id, CAST(:kind AS \"DurableTaskKind\"), "
            'CAST(:status AS "DurableTaskStatus"), :key, CAST(:payload AS jsonb), 0, 3, '
            ":next_retry_at, 0, :completed_at, CURRENT_TIMESTAMP)"
        ),
        {
            "id": identifier,
            "kind": kind,
            "status": status,
            "key": idempotency_key,
            "payload": '{"resourceId": "batch-1"}',
            "next_retry_at": next_retry_at,
            "completed_at": completed_at,
        },
    )
    return identifier


def test_metadata_matches_adr_0018_registry_and_reference_direction() -> None:
    task = metadata.tables["durable_tasks"]
    kind_enum = cast(Enum, task.c.kind.type)
    status_enum = cast(Enum, task.c.status.type)
    assert list(kind_enum.enums) == [item.value for item in DurableTaskKind]
    assert list(status_enum.enums) == [item.value for item in DurableTaskStatus]
    assert {foreign_key.target_fullname for foreign_key in task.foreign_keys} == set()

    for table_name in ("import_batches", "mail_sync_batches"):
        table = metadata.tables[table_name]
        task_id = table.c.taskId
        assert task_id.nullable is False
        assert task_id.unique is True
        foreign_key = next(iter(task_id.foreign_keys))
        assert foreign_key.target_fullname == "durable_tasks.id"
        assert foreign_key.ondelete == "RESTRICT"

    outbox_task_id = metadata.tables["task_outbox"].c.taskId
    assert next(iter(outbox_task_id.foreign_keys)).target_fullname == "durable_tasks.id"


def test_latest_upgrade_has_one_head_and_exact_durable_constraints(
    durable_task_schema: Connection,
) -> None:
    config = Config(ROOT / "alembic.ini")
    # T042 extends the same linear migration chain; durable-task constraints remain
    # unchanged at the latest approved schema head.
    assert ScriptDirectory.from_config(config).get_heads() == ["20260812_0006"]
    inspector = inspect(durable_task_schema)
    assert {"durable_tasks", "task_outbox"}.issubset(inspector.get_table_names())
    checks = {item["name"] for item in inspector.get_check_constraints("durable_tasks")}
    assert checks == {
        "durable_tasks_attempt_count_bounds",
        "durable_tasks_completion_state",
        "durable_tasks_dispatch_generation_nonnegative",
        "durable_tasks_idempotency_key_nonempty",
        "durable_tasks_lease_expiry_after_heartbeat",
        "durable_tasks_lease_state",
        "durable_tasks_payload_object",
        "durable_tasks_retry_schedule_state",
    }


def test_database_rejects_duplicate_idempotency_invalid_state_and_unknown_task(
    durable_task_schema: Connection,
) -> None:
    _insert_task(durable_task_schema)
    durable_task_schema.commit()

    with pytest.raises(IntegrityError), durable_task_schema.begin_nested():
        _insert_task(durable_task_schema, idempotency_key="import:batch-1")

    with pytest.raises(IntegrityError), durable_task_schema.begin_nested():
        _insert_task(
            durable_task_schema,
            idempotency_key="import:running-without-lease",
            status="RUNNING",
        )

    with pytest.raises(IntegrityError), durable_task_schema.begin_nested():
        _insert_task(
            durable_task_schema,
            idempotency_key="import:retry-without-schedule",
            status="RETRY_WAIT",
        )

    with pytest.raises(IntegrityError), durable_task_schema.begin_nested():
        durable_task_schema.execute(
            text(
                'INSERT INTO task_outbox (id, "taskId", "dispatchGeneration") '
                "VALUES (:id, :task_id, 1)"
            ),
            {"id": uuid.uuid4(), "task_id": uuid.uuid4()},
        )

    existing_task_id = durable_task_schema.scalar(
        text('SELECT id FROM durable_tasks WHERE "idempotencyKey" = :key'),
        {"key": "import:batch-1"},
    )
    durable_task_schema.execute(
        text(
            'INSERT INTO task_outbox (id, "taskId", "dispatchGeneration") '
            "VALUES (:id, :task_id, 1)"
        ),
        {"id": uuid.uuid4(), "task_id": existing_task_id},
    )
    durable_task_schema.commit()
    with pytest.raises(IntegrityError), durable_task_schema.begin_nested():
        durable_task_schema.execute(
            text(
                'INSERT INTO task_outbox (id, "taskId", "dispatchGeneration") '
                "VALUES (:id, :task_id, 1)"
            ),
            {"id": uuid.uuid4(), "task_id": existing_task_id},
        )


def test_domain_batch_owns_unique_restricting_task_reference(
    durable_task_schema: Connection,
) -> None:
    task_id = _insert_task(durable_task_schema, idempotency_key="import:owned-task")
    user_id = uuid.uuid4()
    durable_task_schema.execute(
        text(
            'INSERT INTO users (id, username, "passwordHash", "displayName", "updatedAt") '
            "VALUES (:id, :username, :password_hash, :display_name, CURRENT_TIMESTAMP)"
        ),
        {
            "id": user_id,
            "username": f"t041-{user_id}",
            "password_hash": "not-a-real-password-hash",
            "display_name": "T041",
        },
    )
    durable_task_schema.execute(
        text(
            'INSERT INTO import_batches (id, "taskId", "fileName", "fileHash", "storageKey", '
            '"sheetName", "totalRows", "readyRows", "warningRows", "errorRows", '
            '"uploadedById", "sourceExpiresAt", "retentionConfigVersion") VALUES '
            "(:id, :task_id, 'sample.xlsx', :file_hash, 'tests/sample.xlsx', 'Sheet1', "
            "0, 0, 0, 0, :user_id, CURRENT_TIMESTAMP + INTERVAL '365 days', "
            "'ADR0027_DEFAULT')"
        ),
        {
            "id": uuid.uuid4(),
            "task_id": task_id,
            "file_hash": "0" * 64,
            "user_id": user_id,
        },
    )
    durable_task_schema.commit()

    with pytest.raises(IntegrityError), durable_task_schema.begin_nested():
        durable_task_schema.execute(
            text("DELETE FROM durable_tasks WHERE id = :task_id"), {"task_id": task_id}
        )

    with pytest.raises(IntegrityError), durable_task_schema.begin_nested():
        durable_task_schema.execute(
            text(
                'INSERT INTO import_batches (id, "taskId", "fileName", "fileHash", '
                '"storageKey", "sheetName", "totalRows", "readyRows", "warningRows", '
                '"errorRows", "uploadedById", "sourceExpiresAt", '
                '"retentionConfigVersion") VALUES '
                "(:id, :task_id, 'duplicate.xlsx', :file_hash, 'tests/duplicate.xlsx', "
                "'Sheet1', 0, 0, 0, 0, :user_id, CURRENT_TIMESTAMP + INTERVAL '365 days', "
                "'ADR0027_DEFAULT')"
            ),
            {
                "id": uuid.uuid4(),
                "task_id": task_id,
                "file_hash": "1" * 64,
                "user_id": user_id,
            },
        )
