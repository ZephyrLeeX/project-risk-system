from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text

from risk_platform.db import create_database_engine, create_session_factory, dispose_database_engine
from risk_platform.retention.cleanup import RetentionCleanupService
from risk_platform.retention.tasks import enqueue_cleanup

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


@pytest.fixture
def cleanup_schema() -> Iterator[Connection]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL T031 validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t031_{uuid.uuid4().hex}"
    admin = create_engine(sync_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as connection:
            config = Config(ROOT / "alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
            yield connection
            connection.rollback()
            command.check(config)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _async_factory(connection: Connection):  # type: ignore[no-untyped-def]
    schema = connection.scalar(text("SELECT current_schema()"))
    assert isinstance(schema, str)
    url = os.environ["TEST_DATABASE_URL"]
    async_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    engine = create_database_engine(
        f"{async_url}?options=-csearch_path%3D{schema}", pool_pre_ping=False
    )
    return engine, create_session_factory(engine)


def _user(connection: Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    connection.execute(
        text(
            'INSERT INTO users (id, username, "passwordHash", "displayName", "updatedAt") '
            "VALUES (:id, :username, 'hash', 'T031', CURRENT_TIMESTAMP)"
        ),
        {"id": user_id, "username": f"t031-{user_id}"},
    )
    return user_id


def _task(connection: Connection, *, active: bool = False) -> uuid.UUID:
    task_id = uuid.uuid4()
    status_sql = "'QUEUED'" if active else "'SUCCEEDED'"
    completed_sql = "NULL" if active else ":completed"
    connection.execute(
        text(
            'INSERT INTO durable_tasks (id, kind, status, "idempotencyKey", payload, '
            '"maxAttempts", "completedAt", "updatedAt") VALUES '
            f"(:id, 'IMPORT_PREVIEW', {status_sql}, :key, '{{}}'::jsonb, 1, "
            f"{completed_sql}, CURRENT_TIMESTAMP)"
        ),
        {"id": task_id, "key": f"t031-{task_id}", "completed": NOW - timedelta(days=1)},
    )
    return task_id


def _batch(
    connection: Connection,
    *,
    user_id: uuid.UUID,
    storage_root: Path,
    source_exists: bool = True,
    active_task: bool = False,
    hold: bool = False,
) -> uuid.UUID:
    batch_id = uuid.uuid4()
    task_id = _task(connection, active=active_task)
    connection.execute(
        text(
            "INSERT INTO import_batches "
            '(id, "taskId", "fileName", "fileHash", "storageKey", status, "sheetName", '
            '"totalRows", "readyRows", "warningRows", "errorRows", "uploadedById", '
            '"sourceExpiresAt", "retentionConfigVersion", "createdAt") VALUES '
            "(:id, :task, 'source.xlsx', :hash, :storage, 'PREVIEWED', '项目清单', "
            "0, 0, 0, 0, :user, :expires, 'ADR0027_DEFAULT', :created)"
        ),
        {
            "id": batch_id,
            "task": task_id,
            "hash": "a" * 64,
            "storage": f"{batch_id}/source.xlsx",
            "user": user_id,
            "expires": NOW,
            "created": NOW - timedelta(days=365),
        },
    )
    if source_exists:
        target = storage_root / str(batch_id)
        target.mkdir(parents=True)
        (target / "source.xlsx").write_bytes(b"workbook")
    if hold:
        connection.execute(
            text(
                "INSERT INTO retention_holds "
                '(id, "resourceType", "resourceId", reason, "createdById", "createdTraceId") '
                "VALUES (:id, 'IMPORT_BATCH', :resource, 'LEGAL', :user, :trace)"
            ),
            {
                "id": uuid.uuid4(),
                "resource": str(batch_id),
                "user": user_id,
                "trace": str(uuid.uuid4()),
            },
        )
    return batch_id


def _conversation(
    connection: Connection,
    *,
    user_id: uuid.UUID,
    active_confirmation: bool = False,
) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO agent_conversations "
            '(id, "ownerUserId", "createdAt", "updatedAt", "expiresAt", '
            '"retentionConfigVersion") VALUES '
            "(:id, :user, :created, :created, :expires, 'ADR0027_DEFAULT')"
        ),
        {
            "id": conversation_id,
            "user": user_id,
            "created": NOW - timedelta(days=90),
            "expires": NOW,
        },
    )
    if active_confirmation:
        connection.execute(
            text(
                "INSERT INTO agent_confirmation_tokens "
                '(id, "tokenDigest", "ownerUserId", "conversationId", operation, '
                '"canonicalContent", "contentDigest", "scopeDigest", "idempotencyKey", '
                '"issuedAt", "expiresAt") VALUES '
                "(:id, :digest, :user, :conversation, 'REPORT', 'content', :digest, :digest, "
                ":key, :issued, :expires)"
            ),
            {
                "id": uuid.uuid4(),
                "digest": uuid.uuid4().hex + uuid.uuid4().hex,
                "user": user_id,
                "conversation": conversation_id,
                "key": f"confirm-{uuid.uuid4()}",
                "issued": NOW - timedelta(minutes=1),
                "expires": NOW + timedelta(minutes=9),
            },
        )
    return conversation_id


def test_cleanup_deletes_at_boundary_and_preserves_business_facts(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    batch_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    conversation_id = _conversation(cleanup_schema, user_id=user_id)
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        report = await RetentionCleanupService(
            factory, import_storage_root=storage_root, temp_storage_root=temp_root
        ).run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=False)
        assert [item.outcome for item in report.items] == ["DELETED", "DELETED"]
        await dispose_database_engine(engine)

    asyncio.run(scenario())
    assert not (storage_root / str(batch_id) / "source.xlsx").exists()
    assert cleanup_schema.scalar(
        text('SELECT "storageKey" FROM import_batches WHERE id = :id'), {"id": batch_id}
    ) == f"retention-complete:{batch_id}"
    assert cleanup_schema.scalar(
        text("SELECT count(*) FROM agent_conversations WHERE id = :id"),
        {"id": conversation_id},
    ) == 0
    assert (
        cleanup_schema.scalar(
            text("SELECT count(*) FROM import_batches WHERE id = :id"), {"id": batch_id}
        )
        == 1
    )
    assert cleanup_schema.scalar(
        text("SELECT count(*) FROM audit_logs WHERE action = 'RETENTION_ARTIFACT_DELETED'")
    ) == 2


def test_cleanup_rechecks_holds_and_active_confirmations_under_lock(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    batch_id = _batch(
        cleanup_schema, user_id=user_id, storage_root=storage_root, hold=True
    )
    active_batch_id = _batch(
        cleanup_schema,
        user_id=user_id,
        storage_root=storage_root,
        active_task=True,
    )
    conversation_id = _conversation(
        cleanup_schema, user_id=user_id, active_confirmation=True
    )
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        report = await RetentionCleanupService(
            factory, import_storage_root=storage_root, temp_storage_root=temp_root
        ).run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=False)
        outcomes = {item.resource_id: (item.outcome, item.reason) for item in report.items}
        assert outcomes == {
            str(batch_id): ("SKIPPED", "ACTIVE_AUDIT_HOLD"),
            str(active_batch_id): ("SKIPPED", "ACTIVE_OPERATION"),
            str(conversation_id): ("SKIPPED", "ACTIVE_OPERATION"),
        }
        await dispose_database_engine(engine)

    asyncio.run(scenario())
    assert (storage_root / str(batch_id) / "source.xlsx").exists()
    assert (storage_root / str(active_batch_id) / "source.xlsx").exists()
    assert cleanup_schema.scalar(
        text("SELECT count(*) FROM agent_conversations WHERE id = :id"),
        {"id": conversation_id},
    ) == 1
    assert cleanup_schema.scalar(
        text(
            "SELECT count(*) FROM audit_logs "
            "WHERE action = 'RETENTION_CLEANUP_SKIPPED_PROTECTED'"
        )
    ) == 3


def test_partial_failure_is_retryable_without_rolling_back_successful_items(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    good_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    missing_id = _batch(
        cleanup_schema, user_id=user_id, storage_root=storage_root, source_exists=False
    )
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        service = RetentionCleanupService(
            factory, import_storage_root=storage_root, temp_storage_root=temp_root
        )
        report = await service.run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=False)
        assert report.failed is True
        assert {item.resource_id: item.outcome for item in report.items} == {
            str(good_id): "DELETED",
            str(missing_id): "FAILED",
        }
        with pytest.raises(Exception, match="RETENTION_CLEANUP_PARTIAL_FAILURE"):
            await service.handle(
                {
                    "as_of": NOW.isoformat(),
                    "trace_id": str(uuid.uuid4()),
                    "dry_run": False,
                }
            )
        await dispose_database_engine(engine)

    asyncio.run(scenario())
    assert cleanup_schema.scalar(
        text('SELECT "storageKey" FROM import_batches WHERE id = :id'), {"id": good_id}
    ) == f"retention-complete:{good_id}"
    assert cleanup_schema.scalar(
        text('SELECT "storageKey" FROM import_batches WHERE id = :id'), {"id": missing_id}
    ) == f"{missing_id}/source.xlsx"
    assert cleanup_schema.scalar(
        text("SELECT count(*) FROM audit_logs WHERE action = 'RETENTION_CLEANUP_FAILED'")
    ) >= 1


def test_dry_run_and_durable_task_creation_are_idempotent(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    batch_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        report = await RetentionCleanupService(
            factory, import_storage_root=storage_root, temp_storage_root=temp_root
        ).run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=True)
        assert [(item.outcome, item.resource_id) for item in report.items] == [
            ("ELIGIBLE", str(batch_id))
        ]
        async with factory() as session, session.begin():
            first = await enqueue_cleanup(
                session, as_of=NOW, trace_id=uuid.uuid4(), dry_run=False
            )
            second = await enqueue_cleanup(
                session, as_of=NOW, trace_id=uuid.uuid4(), dry_run=False
            )
            assert first.id == second.id
        await dispose_database_engine(engine)

    asyncio.run(scenario())
    assert (storage_root / str(batch_id) / "source.xlsx").exists()
    assert cleanup_schema.scalar(
        text("SELECT count(*) FROM durable_tasks WHERE kind = 'RETENTION_CLEANUP'")
    ) == 1
    assert cleanup_schema.scalar(text("SELECT count(*) FROM task_outbox")) == 1


def test_tombstone_marker_left_after_commit_is_reconciled_on_next_run(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    batch_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    batch_dir = storage_root / str(batch_id)
    (batch_dir / "source.xlsx").unlink()
    marker = batch_dir / ".retention-delete"
    marker.write_bytes(b"")
    cleanup_schema.execute(
        text('UPDATE import_batches SET "storageKey" = :key WHERE id = :id'),
        {"id": batch_id, "key": f"retention-deleted:{batch_id}"},
    )
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        report = await RetentionCleanupService(
            factory, import_storage_root=storage_root, temp_storage_root=temp_root
        ).run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=False)
        assert [(item.resource_id, item.outcome) for item in report.items] == [
            (str(batch_id), "DELETED")
        ]
        await dispose_database_engine(engine)

    asyncio.run(scenario())
    assert not marker.exists()
    assert not batch_dir.exists()
    assert cleanup_schema.scalar(
        text('SELECT "storageKey" FROM import_batches WHERE id = :id'), {"id": batch_id}
    ) == f"retention-complete:{batch_id}"


def test_clean_tombstones_do_not_starve_due_source_candidates(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    # Insert more old, clean tombstones than the service candidate limit.
    for _ in range(3):
        old_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
        (storage_root / str(old_id) / "source.xlsx").unlink()
        (storage_root / str(old_id)).rmdir()
        cleanup_schema.execute(
            text(
                'UPDATE import_batches SET "storageKey" = :key, "sourceExpiresAt" = :expires '
                "WHERE id = :id"
            ),
            {
                "id": old_id,
                "key": f"retention-complete:{old_id}",
                "expires": NOW - timedelta(days=1),
            },
        )
    due_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        report = await RetentionCleanupService(
            factory,
            import_storage_root=storage_root,
            temp_storage_root=temp_root,
            candidate_limit=1,
        ).run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=False)
        assert [(item.resource_id, item.outcome) for item in report.items] == [
            (str(due_id), "DELETED")
        ]
        await dispose_database_engine(engine)

    asyncio.run(scenario())


def test_failed_recovery_does_not_starve_normal_due_source(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    recovery_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    recovery_dir = storage_root / str(recovery_id)
    (recovery_dir / ".retention-delete").write_bytes(b"")
    cleanup_schema.execute(
        text('UPDATE import_batches SET "storageKey" = :key WHERE id = :id'),
        {"id": recovery_id, "key": f"retention-deleted:{recovery_id}"},
    )
    due_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        report = await RetentionCleanupService(
            factory,
            import_storage_root=storage_root,
            temp_storage_root=temp_root,
            candidate_limit=1,
        ).run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=False)
        assert {item.resource_id: item.outcome for item in report.items} == {
            str(recovery_id): "FAILED",
            str(due_id): "DELETED",
        }
        await dispose_database_engine(engine)

    asyncio.run(scenario())
    assert (recovery_dir / ".retention-delete").exists()
    assert (recovery_dir / "source.xlsx").exists()
    assert not (storage_root / str(due_id) / "source.xlsx").exists()


def test_tombstone_recovery_dry_run_does_not_mutate_marker(
    cleanup_schema: Connection, tmp_path: Path
) -> None:
    storage_root = (tmp_path / "imports").resolve()
    temp_root = (tmp_path / "temp").resolve()
    storage_root.mkdir()
    temp_root.mkdir()
    user_id = _user(cleanup_schema)
    batch_id = _batch(cleanup_schema, user_id=user_id, storage_root=storage_root)
    batch_dir = storage_root / str(batch_id)
    (batch_dir / "source.xlsx").unlink()
    marker = batch_dir / ".retention-delete"
    marker.write_bytes(b"")
    cleanup_schema.execute(
        text('UPDATE import_batches SET "storageKey" = :key WHERE id = :id'),
        {"id": batch_id, "key": f"retention-deleted:{batch_id}"},
    )
    cleanup_schema.commit()
    engine, factory = _async_factory(cleanup_schema)

    async def scenario() -> None:
        report = await RetentionCleanupService(
            factory, import_storage_root=storage_root, temp_storage_root=temp_root
        ).run(as_of=NOW, trace_id=uuid.uuid4(), dry_run=True)
        assert [(item.resource_id, item.outcome) for item in report.items] == [
            (str(batch_id), "ELIGIBLE")
        ]
        await dispose_database_engine(engine)

    asyncio.run(scenario())
    assert marker.exists()
