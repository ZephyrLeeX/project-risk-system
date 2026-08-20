from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

from risk_platform.app import AppComposition, create_app
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, dispose_database_engine
from risk_platform.retention.api import get_retention_hold_service, router
from risk_platform.retention.models import RetentionHoldReason, RetentionResourceType
from risk_platform.retention.service import (
    LockedRetentionProtectionService,
    RetentionDecision,
    RetentionHoldService,
)
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def retention_schema() -> Iterator[Connection]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL T042 validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t042_{uuid.uuid4().hex}"
    admin = create_engine(sync_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as connection:
            config = Config(ROOT / "alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "20260811_0005")
            _insert_legacy_facts(connection)
            connection.commit()
            command.upgrade(config, "head")
            connection.commit()
            yield connection
            command.check(config)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _insert_legacy_facts(connection: Connection) -> None:
    user_id, task_id, batch_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    confirmed_at = created_at + timedelta(days=4)
    connection.execute(
        text(
            'INSERT INTO users (id, username, "passwordHash", "displayName", "updatedAt") '
            "VALUES (:id, :username, 'hash', 'T042', CURRENT_TIMESTAMP)"
        ),
        {"id": user_id, "username": f"t042-{user_id}"},
    )
    connection.execute(
        text(
            'INSERT INTO durable_tasks (id, kind, "idempotencyKey", '
            'payload, "maxAttempts", "updatedAt") '
            "VALUES (:id, 'IMPORT_PREVIEW', :key, '{}'::jsonb, 1, CURRENT_TIMESTAMP)"
        ),
        {"id": task_id, "key": f"t042-{task_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO import_batches "
            '(id, "taskId", "fileName", "fileHash", "storageKey", status, "sheetName", '
            '"totalRows", "readyRows", "warningRows", "errorRows", "uploadedById", "createdAt", '
            '"confirmedAt") VALUES '
            "(:id, :task, 'source.xlsx', :hash, 'key', 'IMPORTED', '项目清单', 0, 0, 0, 0, :user, "
            ":created, :confirmed)"
        ),
        {
            "id": batch_id,
            "task": task_id,
            "hash": "a" * 64,
            "user": user_id,
            "created": created_at,
            "confirmed": confirmed_at,
        },
    )


def test_migration_backfills_defaults_and_active_hold_constraint(
    retention_schema: Connection,
) -> None:
    batch = (
        retention_schema.execute(
            text(
                'SELECT "sourceExpiresAt", "rollbackProtectedUntil", "retentionConfigVersion" '
                "FROM import_batches"
            )
        )
        .mappings()
        .one()
    )
    assert batch["sourceExpiresAt"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert batch["rollbackProtectedUntil"] == datetime(2025, 2, 4, tzinfo=UTC)
    assert batch["retentionConfigVersion"] == "ADR0027_DEFAULT"

    user_id = retention_schema.scalar(text("SELECT id FROM users LIMIT 1"))
    assert user_id is not None
    values = {"id": uuid.uuid4(), "user": user_id, "trace": str(uuid.uuid4())}
    statement = text(
        "INSERT INTO retention_holds "
        '(id, "resourceType", "resourceId", reason, "createdById", '
        "\"createdTraceId\") VALUES (:id, 'IMPORT_BATCH', 'copy-1', 'LEGAL', :user, :trace)"
    )
    retention_schema.execute(statement, values)
    with pytest.raises(IntegrityError):
        retention_schema.execute(statement, {**values, "id": uuid.uuid4()})
    retention_schema.rollback()


def test_expiring_hold_writes_terminal_state_and_audit(retention_schema: Connection) -> None:
    schema = retention_schema.scalar(text("SELECT current_schema()"))
    assert isinstance(schema, str)
    url = os.environ["TEST_DATABASE_URL"]
    async_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    engine = create_database_engine(
        f"{async_url}?options=-csearch_path%3D{schema}", pool_pre_ping=False
    )
    factory = create_session_factory(engine)
    resource_id = str(retention_schema.scalar(text("SELECT id FROM import_batches LIMIT 1")))
    user_id = retention_schema.scalar(text("SELECT id FROM users LIMIT 1"))
    assert user_id is not None
    trace_id = uuid.uuid4()
    now = datetime(2026, 8, 12, tzinfo=UTC)
    retention_schema.execute(
        text(
            "INSERT INTO retention_holds "
            '(id, "resourceType", "resourceId", reason, "createdById", '
            '"createdTraceId", "createdAt", "expiresAt") VALUES '
            "(:id, 'IMPORT_BATCH', :resource, 'LEGAL', "
            ":user, :trace, :created, :expires)"
        ),
        {
            "id": uuid.uuid4(),
            "resource": resource_id,
            "user": user_id,
            "trace": str(trace_id),
            "created": now - timedelta(days=2),
            "expires": now - timedelta(seconds=1),
        },
    )
    retention_schema.commit()

    async def exercise() -> None:
        from risk_platform.db import transaction

        async with transaction(factory) as session:
            await RetentionHoldService(factory)._lock_resource(
                session, RetentionResourceType.IMPORT_BATCH, resource_id
            )
            await RetentionHoldService(factory)._lock_resource_fact(
                session, RetentionResourceType.IMPORT_BATCH, resource_id
            )
            expired = await RetentionHoldService(factory).expire_due_locked(
                session,
                resource_type=RetentionResourceType.IMPORT_BATCH,
                resource_id=resource_id,
                as_of=now,
                trace_id=uuid.uuid4(),
            )
            assert expired is True
        await dispose_database_engine(engine)

    asyncio.run(exercise())
    state = (
        retention_schema.execute(
            text('SELECT status, "expiredAt" FROM retention_holds WHERE "resourceId" = :resource'),
            {"resource": resource_id},
        )
        .mappings()
        .one()
    )
    assert state["status"] == "EXPIRED"
    assert state["expiredAt"] == now
    assert (
        retention_schema.scalar(
            text("SELECT count(*) FROM audit_logs WHERE action = 'RETENTION_HOLD_EXPIRED'")
        )
        == 1
    )


def test_database_trigger_prohibits_terminal_reactivation_and_delete(
    retention_schema: Connection,
) -> None:
    user_id = retention_schema.scalar(text("SELECT id FROM users LIMIT 1"))
    assert user_id is not None
    hold_id = uuid.uuid4()
    resource_id = str(retention_schema.scalar(text("SELECT id FROM import_batches LIMIT 1")))
    retention_schema.execute(
        text(
            "INSERT INTO retention_holds "
            '(id, "resourceType", "resourceId", reason, "createdById", "createdTraceId") '
            "VALUES (:id, 'IMPORT_BATCH', :resource, 'LEGAL', :user, :trace)"
        ),
        {"id": hold_id, "resource": resource_id, "user": user_id, "trace": str(uuid.uuid4())},
    )
    retention_schema.execute(
        text(
            "UPDATE retention_holds SET status = 'RELEASED', \"releasedAt\" = CURRENT_TIMESTAMP, "
            '"releasedById" = :user, "releasedTraceId" = :trace WHERE id = :id'
        ),
        {"id": hold_id, "user": user_id, "trace": str(uuid.uuid4())},
    )
    retention_schema.commit()
    with pytest.raises(Exception, match="terminal retention hold"):
        retention_schema.execute(
            text(
                "UPDATE retention_holds SET status = 'ACTIVE', \"releasedAt\" = NULL, "
                '"releasedById" = NULL, "releasedTraceId" = NULL WHERE id = :id'
            ),
            {"id": hold_id},
        )
    retention_schema.rollback()
    with pytest.raises(Exception, match="retention hold deletion"):
        retention_schema.execute(
            text("DELETE FROM retention_holds WHERE id = :id"), {"id": hold_id}
        )
    retention_schema.rollback()


def test_locked_protection_rechecks_resource_and_hold_facts(
    retention_schema: Connection,
) -> None:
    schema = retention_schema.scalar(text("SELECT current_schema()"))
    batch_id = retention_schema.scalar(text("SELECT id FROM import_batches LIMIT 1"))
    assert isinstance(schema, str) and batch_id is not None
    url = os.environ["TEST_DATABASE_URL"]
    async_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    engine = create_database_engine(
        f"{async_url}?options=-csearch_path%3D{schema}", pool_pre_ping=False
    )
    factory = create_session_factory(engine)

    async def exercise() -> None:
        result = await LockedRetentionProtectionService(factory).import_batch(
            batch_id=batch_id,
            active_operation=False,
            as_of=datetime(2027, 1, 1, tzinfo=UTC),
            trace_id=uuid.uuid4(),
        )
        assert result.decision is RetentionDecision.ELIGIBLE

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(dispose_database_engine(engine))


def test_hold_management_api_contract_and_idempotency(retention_schema: Connection) -> None:
    schema = retention_schema.scalar(text("SELECT current_schema()"))
    user_id = retention_schema.scalar(text("SELECT id FROM users LIMIT 1"))
    batch_id = retention_schema.scalar(text("SELECT id FROM import_batches LIMIT 1"))
    assert isinstance(schema, str) and user_id is not None and batch_id is not None
    url = os.environ["TEST_DATABASE_URL"]
    async_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    engine = create_database_engine(
        f"{async_url}?options=-csearch_path%3D{schema}", pool_pre_ping=False
    )
    factory = create_session_factory(engine)

    async def scenario() -> None:
        identity = SessionIdentity(
            session_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            user=AuthenticatedUser(
                id=str(user_id),
                username="t042",
                displayName="T042",
                departmentName=None,
                roleCodes=["SYSTEM_ADMIN"],
                permissions=["admin.config.manage"],
                dataScope="ALL",
                mustChangePassword=False,
            ),
        )

        async def override_identity() -> SessionIdentity:
            return identity

        service = RetentionHoldService(factory)
        app = create_app(
            Settings(environment="test", cors_origins=("https://web.internal",)),
            AppComposition(
                routers=(router,),
                dependency_overrides={
                    current_identity: override_identity,
                    get_retention_hold_service: lambda: service,
                },
            ),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            payload = {
                "resourceType": "IMPORT_BATCH",
                "resourceId": str(batch_id),
                "reason": "LEGAL",
                "expiresAt": None,
            }
            created = await client.post(
                "/api/admin/retention-holds",
                headers={"origin": "https://web.internal"},
                json=payload,
            )
            assert created.status_code == 201
            assert created.json()["code"] == "OK"
            hold = created.json()["data"]
            assert set(hold) == {
                "id",
                "resourceType",
                "resourceId",
                "reason",
                "status",
                "createdAt",
                "createdById",
                "expiresAt",
                "releasedAt",
                "releasedById",
                "expiredAt",
                "expiredById",
            }
            assert hold["createdAt"].endswith("Z")
            retried = await client.post(
                "/api/admin/retention-holds",
                headers={"origin": "https://web.internal"},
                json=payload,
            )
            assert retried.status_code == 200
            assert retried.json()["data"]["id"] == hold["id"]
            conflict = await client.post(
                "/api/admin/retention-holds",
                headers={"origin": "https://web.internal"},
                json={**payload, "reason": "INCIDENT"},
            )
            assert conflict.status_code == 409
            assert conflict.json()["code"] == "RETENTION_HOLD_ALREADY_ACTIVE"
            listed = await client.get("/api/admin/retention-holds?page=1&pageSize=30")
            assert listed.status_code == 200
            assert listed.json()["data"]["total"] == 1
            detail = await client.get(f"/api/admin/retention-holds/{hold['id']}")
            assert detail.status_code == 200
            released = await client.post(
                f"/api/admin/retention-holds/{hold['id']}/release",
                headers={"origin": "https://web.internal"},
                json={},
            )
            assert released.status_code == 200
            assert released.json()["data"]["status"] == "RELEASED"
            assert (
                await client.post(
                    f"/api/admin/retention-holds/{hold['id']}/release",
                    headers={"origin": "https://web.internal"},
                    json={},
                )
            ).status_code == 200
            unavailable = await client.post(
                "/api/admin/retention-holds",
                headers={"origin": "https://web.internal"},
                json={
                    "resourceType": "BACKUP_COPY",
                    "resourceId": "backup-copy-1",
                    "reason": "LEGAL",
                    "expiresAt": None,
                },
            )
            assert unavailable.status_code == 409
            assert unavailable.json()["code"] == "RETENTION_BACKUP_COPY_UNAVAILABLE"
            invalid = await client.post(
                "/api/admin/retention-holds",
                headers={"origin": "https://web.internal"},
                json={**payload, "extra": True},
            )
            assert invalid.status_code == 422
            unauthorized = SessionIdentity(
                session_id=uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                user=identity.user.model_copy(update={"permissions": []}),
            )
            with pytest.raises(ApiError) as denied:
                await RetentionHoldService(factory).create(
                    resource_type=RetentionResourceType.IMPORT_BATCH,
                    resource_id=str(batch_id),
                    reason=RetentionHoldReason.LEGAL,
                    expires_at=None,
                    identity=unauthorized,
                    trace_id=uuid.uuid4(),
                    as_of=datetime.now(UTC),
                )
            assert denied.value.code == "FORBIDDEN"
            async with factory() as session:
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM audit_logs "
                            "WHERE action = 'RETENTION_HOLD_CREATED'"
                        )
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM audit_logs "
                            "WHERE action = 'RETENTION_HOLD_CHANGE_FAILED' "
                            "AND \"failureCode\" = 'FORBIDDEN'"
                        )
                    )
                    >= 1
                )
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM audit_logs "
                            "WHERE action = 'RETENTION_HOLD_RELEASED'"
                        )
                    )
                    == 1
                )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(dispose_database_engine(engine))
