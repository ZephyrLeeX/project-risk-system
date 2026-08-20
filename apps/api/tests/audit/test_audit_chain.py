from __future__ import annotations

import asyncio
import inspect
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect

from risk_platform.audit import AuditActorType, AuditEvent, AuditResult, AuditService
from risk_platform.audit.models import AuditLog
from risk_platform.db import create_database_engine, create_session_factory, transaction

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def audit_schema() -> Iterator[tuple[str, str]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL audit validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t006_{uuid.uuid4().hex}"
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
            command.check(config)
        yield sync_url, schema
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _async_url(sync_url: str, schema: str) -> str:
    url = sync_url.replace("postgresql+psycopg://", "postgresql+psycopg://", 1)
    return f"{url}?options=-csearch_path%3D{schema}"


def _success_event(*, sequence: int = 1) -> AuditEvent:
    return AuditEvent(
        actor_type=AuditActorType.SYSTEM,
        module="AUDIT",
        action="INTEGRITY.PROBE",
        resource_type="AUDIT_LOG",
        resource_id=f"probe-{sequence}",
        result=AuditResult.SUCCESS,
        trace_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )


def test_audit_input_is_closed_and_contains_no_payload_channel() -> None:
    fields = set(AuditEvent.model_fields)
    assert fields == {
        "actor_id",
        "actor_type",
        "module",
        "action",
        "resource_type",
        "resource_id",
        "trace_id",
        "request_id",
        "project_id",
        "failure_code",
        "result",
    }
    forbidden = {
        "snapshot",
        "before_snapshot",
        "after_snapshot",
        "metadata",
        "payload",
        "request_body",
        "response_body",
        "mail_body",
        "attachment_content",
        "prompt",
        "model_response",
        "secret",
    }
    assert fields.isdisjoint(forbidden)
    base: dict[str, Any] = {
        "actor_type": AuditActorType.SYSTEM,
        "module": "AUDIT",
        "action": "WRITE",
        "resource_type": "AUDIT_LOG",
        "result": AuditResult.SUCCESS,
        "trace_id": uuid.uuid4(),
    }
    for name in forbidden:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            AuditEvent.model_validate(base | {name: {"password": "secret"}})

    for field, value in {
        "module": "mail body",
        "action": "prompt=ignore_previous",
        "resource_type": "raw model response",
        "resource_id": "body@example.com contains spaces",
        "failure_code": "token=secret",
    }.items():
        with pytest.raises(ValidationError):
            AuditEvent.model_validate(base | {field: value})

    for method_name in ("record", "record_success", "record_failure"):
        parameters = inspect.signature(getattr(AuditService, method_name)).parameters.values()
        assert all(parameter.kind is not inspect.Parameter.VAR_KEYWORD for parameter in parameters)


def test_audit_orm_has_no_json_or_content_columns() -> None:
    columns = set(AuditLog.__table__.columns.keys())
    assert columns == {
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
    assert all(column.type.__class__.__name__ != "JSONB" for column in AuditLog.__table__.columns)


def test_migration_schema_contains_only_typed_metadata(audit_schema: tuple[str, str]) -> None:
    sync_url, schema = audit_schema
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        inspector = sa_inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("audit_logs")}
        assert columns == set(AuditLog.__table__.columns.keys())
        assert not columns & {
            "beforeSnapshot",
            "afterSnapshot",
            "metadata",
            "payload",
            "clientIp",
            "userAgent",
            "summary",
            "isSensitive",
        }
    finally:
        engine.dispose()


def test_commit_rollback_and_valid_chain(audit_schema: tuple[str, str]) -> None:
    sync_url, schema = audit_schema

    async def exercise() -> None:
        engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
        factory = create_session_factory(engine)
        async with transaction(factory) as session:
            service = AuditService(session)
            await service.record(_success_event(sequence=1))
        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(factory) as session:
                await AuditService(session).record(_success_event(sequence=2))
                raise RuntimeError("rollback")
        async with factory() as session:
            integrity = await AuditService(session).verify_integrity()
            assert integrity.status == "VALID"
            assert integrity.total_records == 1
            assert integrity.verified_records == 1
        await engine.dispose()

    asyncio.run(exercise())


def test_concurrent_appends_form_one_chain(audit_schema: tuple[str, str]) -> None:
    sync_url, schema = audit_schema

    async def exercise() -> None:
        engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
        factory = create_session_factory(engine)

        async def append(sequence: int) -> None:
            async with transaction(factory) as session:
                await AuditService(session).record(_success_event(sequence=sequence))

        await asyncio.gather(*(append(index) for index in range(20)))
        async with factory() as session:
            integrity = await AuditService(session).verify_integrity()
            assert integrity.status == "VALID"
            assert integrity.total_records == 20
            assert integrity.verified_records == 20
            fork_count = await session.scalar(
                text(
                    'SELECT count(*) FROM ('
                    'SELECT "previousHash" FROM "audit_logs" '
                    'WHERE "previousHash" IS NOT NULL GROUP BY "previousHash" HAVING count(*) > 1'
                    ') AS forks'
                )
            )
            assert fork_count == 0
        await engine.dispose()

    asyncio.run(exercise())


def test_same_transaction_burst_has_strict_append_order(
    audit_schema: tuple[str, str],
) -> None:
    sync_url, schema = audit_schema
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    descending_ids = [uuid.UUID(int=index) for index in range(200, 0, -1)]
    try:
        with engine.begin() as connection:
            for index, event_id in enumerate(descending_ids):
                connection.execute(
                    text(
                        '''INSERT INTO "audit_logs"
                        ("id", "actorType", "module", "action", "resourceType", "resourceId",
                         "result", "traceId", "createdAt")
                        VALUES (:id, 'SYSTEM', 'AUDIT', 'BURST.WRITE', 'AUDIT_LOG', :resource_id,
                                'SUCCESS', :trace_id, CURRENT_TIMESTAMP)'''
                    ),
                    {
                        "id": event_id,
                        "resource_id": f"burst-{index}",
                        "trace_id": str(uuid.uuid4()),
                    },
                )
            timestamps = connection.execute(
                text('SELECT "createdAt" FROM "audit_logs" ORDER BY "createdAt", "id"')
            ).scalars().all()
            assert timestamps == sorted(set(timestamps))

        async def verify() -> None:
            async_engine = create_database_engine(
                _async_url(sync_url, schema), pool_pre_ping=False
            )
            factory = create_session_factory(async_engine)
            async with factory() as session:
                integrity = await AuditService(session).verify_integrity()
                assert integrity.status == "VALID"
                assert integrity.total_records == 200
                assert integrity.verified_records == 200
            await async_engine.dispose()

        asyncio.run(verify())
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        'UPDATE "audit_logs" SET "action" = \'ALTERED\'',
        'DELETE FROM "audit_logs"',
        'TRUNCATE "audit_logs"',
    ],
)
def test_database_rejects_all_mutation_forms(
    audit_schema: tuple[str, str], statement: str
) -> None:
    sync_url, schema = audit_schema
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    '''INSERT INTO "audit_logs"
                    ("id", "actorType", "module", "action", "resourceType", "resourceId",
                     "result", "traceId", "createdAt")
                    VALUES (:id, 'SYSTEM', 'AUDIT', 'WRITE', 'AUDIT_LOG', 'probe',
                            'SUCCESS', :trace_id, CURRENT_TIMESTAMP)'''
                ),
                {"id": uuid.uuid4(), "trace_id": str(uuid.uuid4())},
            )
        with engine.begin() as connection, pytest.raises(Exception, match="append-only"):
            connection.execute(text(statement))
    finally:
        engine.dispose()


def test_verifier_detects_tampering_and_never_repairs(audit_schema: tuple[str, str]) -> None:
    sync_url, schema = audit_schema

    async def seed_and_verify() -> None:
        engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
        factory = create_session_factory(engine)
        async with transaction(factory) as session:
            await AuditService(session).record(_success_event(sequence=1))
            await AuditService(session).record(_success_event(sequence=2))
        await engine.dispose()

    asyncio.run(seed_and_verify())
    sync_engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with sync_engine.begin() as connection:
            original_hash = connection.scalar(
                text('SELECT "integrityHash" FROM "audit_logs" ORDER BY "createdAt", "id" LIMIT 1')
            )
            connection.execute(
                text('ALTER TABLE "audit_logs" DISABLE TRIGGER "audit_logs_reject_update"')
            )
            connection.execute(
                text(
                    'UPDATE "audit_logs" SET "action" = \'TAMPERED\' '
                    'WHERE "id" = (SELECT "id" FROM "audit_logs" '
                    'ORDER BY "createdAt", "id" LIMIT 1)'
                )
            )
            connection.execute(
                text('ALTER TABLE "audit_logs" ENABLE TRIGGER "audit_logs_reject_update"')
            )

        async def verify() -> None:
            engine = create_database_engine(_async_url(sync_url, schema), pool_pre_ping=False)
            factory = create_session_factory(engine)
            async with factory() as session:
                first = await AuditService(session).verify_integrity()
                second = await AuditService(session).verify_integrity()
                assert first.status == second.status == "INVALID"
                assert first.first_broken_event_id is not None
            await engine.dispose()

        asyncio.run(verify())
        with sync_engine.connect() as connection:
            assert connection.scalar(
                text('SELECT "integrityHash" FROM "audit_logs" ORDER BY "createdAt", "id" LIMIT 1')
            ) == original_hash
    finally:
        sync_engine.dispose()
