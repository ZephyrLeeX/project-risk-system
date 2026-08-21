"""T060 — user-initiated conversation soft delete (hide from "my history").

Pins the delete contract:

* only the owner may delete, and only a terminal conversation (no RUNNING
  execution with an active durable task, no WAITING_FOR_USER, no OPEN
  interaction) — a live turn answers 409 ``AGENT_CONVERSATION_BUSY`` and is
  never implicitly cancelled;
* after deletion every owner-scoped surface (list, history, messages,
  continue, cancel, events) answers the same 404 as a missing conversation;
* the underlying rows (conversation, messages) remain — the durable fact graph
  stays retained and the retention cleanup worker still owns the physical
  lifecycle via ``expiresAt`` (ADR 0012).
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.api import get_agent_service
from risk_platform.agent.api import router as agent_router
from risk_platform.agent.models import (
    AgentConversation,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    AgentMessageRole,
)
from risk_platform.agent.service import AgentConversationService
from risk_platform.app import AppComposition, create_app
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind, DurableTaskStatus
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]
OWNER = UUID("00000000-0000-0000-0000-000000000080")
OTHER = UUID("00000000-0000-0000-0000-000000000081")

_PROJECT_CANDIDATE = {
    "id": "00000000-0000-0000-0000-000000000040",
    "name": "南岸项目",
    "externalCode": "P-NAN",
    "departmentName": "工程部",
    "status": "DELIVERY",
}


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Agent validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t060_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()
    engine = create_database_engine(f"{sync_url}?options=-csearch_path%3D{schema}")
    factory = create_session_factory(engine)
    try:
        asyncio.run(_seed(factory))
        yield factory
    finally:
        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(factory) as session:
        session.add_all(
            (
                User(
                    id=OWNER,
                    username="t060-owner",
                    passwordHash="not-a-real-password-hash",
                    displayName="T060 Owner",
                ),
                User(
                    id=OTHER,
                    username="t060-other",
                    passwordHash="not-a-real-password-hash",
                    displayName="T060 Other",
                ),
            )
        )


def identity(user_id: UUID = OWNER, permissions: list[str] | None = None) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(user_id),
            username="t060",
            displayName="T060",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=permissions or ["agent.use", "dashboard.view"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


def service(database: async_sessionmaker[AsyncSession]) -> AgentConversationService:
    return AgentConversationService(database, trace_id=lambda: "t060-trace")


async def _seed_terminal_conversation(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner: UUID,
    first_user_message: str,
) -> UUID:
    """Seed a finished conversation (terminal execution, no open interaction)."""

    async with transaction(factory) as session:
        now = datetime.now(UTC)
        conversation = AgentConversation(
            ownerUserId=owner,
            createdAt=now,
            updatedAt=now,
            expiresAt=now + timedelta(days=90),
            retentionConfigVersion="test",
        )
        session.add(conversation)
        await session.flush()
        message = AgentMessage(
            conversationId=conversation.id,
            sequence=1,
            role=AgentMessageRole.USER,
            content=first_user_message,
            traceId="t060-trace",
            dataAsOf=now,
        )
        session.add(message)
        await session.flush()
        task = await enqueue_task(
            session,
            DurableTaskKind.AGENT_EXECUTION,
            f"agent-execution-test:{conversation.id}",
            {
                "conversation_id": str(conversation.id),
                "user_message_id": str(message.id),
            },
        )
        execution = AgentExecution(
            conversationId=conversation.id,
            taskId=task.id,
            userMessageId=message.id,
            requestedByUserId=owner,
            status=AgentExecutionStatus.COMPLETED,
            completedAt=now,
        )
        session.add(execution)
        task.status = DurableTaskStatus.SUCCEEDED
        task.completedAt = now
        await session.flush()
        session.add(
            AgentExecutionConfig(
                id=uuid.uuid4(),
                taskId=task.id,
                conversationId=conversation.id,
                userMessageId=message.id,
                requestedByUserId=owner,
                timeoutSeconds=90,
            )
        )
        return conversation.id


async def _seed_waiting_for_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner: UUID,
) -> UUID:
    """Seed a conversation paused on an OPEN interaction (no worker required)."""

    async with transaction(factory) as session:
        now = datetime.now(UTC)
        conversation = AgentConversation(
            ownerUserId=owner,
            createdAt=now,
            updatedAt=now,
            expiresAt=now + timedelta(days=90),
            retentionConfigVersion="test",
        )
        session.add(conversation)
        await session.flush()
        message = AgentMessage(
            conversationId=conversation.id,
            sequence=1,
            role=AgentMessageRole.USER,
            content="查询本周风险",
            traceId="t060-trace",
            dataAsOf=now,
        )
        session.add(message)
        await session.flush()
        task = await enqueue_task(
            session,
            DurableTaskKind.AGENT_EXECUTION,
            f"agent-execution-test:{conversation.id}",
            {
                "conversation_id": str(conversation.id),
                "user_message_id": str(message.id),
            },
        )
        execution = AgentExecution(
            conversationId=conversation.id,
            taskId=task.id,
            userMessageId=message.id,
            requestedByUserId=owner,
            status=AgentExecutionStatus.WAITING_FOR_USER,
        )
        session.add(execution)
        await session.flush()
        session.add(
            AgentExecutionConfig(
                id=uuid.uuid4(),
                taskId=task.id,
                conversationId=conversation.id,
                userMessageId=message.id,
                requestedByUserId=owner,
                timeoutSeconds=90,
            )
        )
        session.add(
            AgentInteraction(
                executionId=execution.id,
                conversationId=conversation.id,
                ownerUserId=owner,
                type=AgentInteractionType.PROJECT_SELECTION,
                status=AgentInteractionStatus.OPEN,
                candidateOptions=[_PROJECT_CANDIDATE],
                expiresAt=now + timedelta(minutes=30),
            )
        )
        return conversation.id


async def _hidden_row(
    factory: async_sessionmaker[AsyncSession], conversation_id: UUID
) -> AgentConversation | None:
    async with factory() as session:
        return await session.get(AgentConversation, conversation_id)


def test_owner_delete_hides_conversation_from_every_surface_and_keeps_rows(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        app_service = service(database)
        conversation_id = await _seed_terminal_conversation(
            database, owner=OWNER, first_user_message="列出本周高风险项目"
        )

        # Before deletion the conversation is fully visible.
        page = await app_service.list_conversations(identity(OWNER), page=1, pageSize=20)
        assert conversation_id in {item.id for item in page.items}
        history = await app_service.history(identity(OWNER), conversation_id)
        assert history.conversation.id == conversation_id

        await app_service.delete(identity(OWNER), conversation_id, uuid.uuid4())

        # List no longer returns it and the total drops.
        page = await app_service.list_conversations(identity(OWNER), page=1, pageSize=20)
        assert conversation_id not in {item.id for item in page.items}

        # Every owner-scoped read answers the SAME 404 as a missing row —
        # nothing leaks that a retained row exists behind the hide marker.
        for call in (
            lambda: app_service.history(identity(OWNER), conversation_id),
            lambda: app_service.message_page(
                identity(OWNER), conversation_id, after_sequence=0, limit=10
            ),
            lambda: app_service.continue_conversation(identity(OWNER), conversation_id, "继续"),
            lambda: app_service.cancel(identity(OWNER), conversation_id),
            lambda: app_service.events(identity(OWNER), conversation_id, None, None),
        ):
            with pytest.raises(ApiError) as raised:
                await call()
            assert raised.value.status_code == 404
            assert raised.value.code == "AGENT_CONVERSATION_NOT_FOUND"

        # The durable facts are retained: the row (with the hide marker) and
        # its messages still exist; nothing was cascade-deleted.
        row = await _hidden_row(database, conversation_id)
        assert row is not None
        assert row.deletedAt is not None
        async with database() as session:
            message_count = len(
                (
                    await session.scalars(
                        select(AgentMessage).where(
                            AgentMessage.conversationId == conversation_id
                        )
                    )
                ).all()
            )
        assert message_count == 1

        # Repeat delete of the hidden row is indistinguishable from missing.
        with pytest.raises(ApiError) as raised:
            await app_service.delete(identity(OWNER), conversation_id, uuid.uuid4())
        assert raised.value.status_code == 404

    asyncio.run(scenario())


def test_delete_is_owner_scoped(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        app_service = service(database)
        conversation_id = await _seed_terminal_conversation(
            database, owner=OWNER, first_user_message="只有所有者能删除"
        )

        with pytest.raises(ApiError) as raised:
            await app_service.delete(identity(OTHER), conversation_id, uuid.uuid4())
        assert raised.value.status_code == 404
        assert raised.value.code == "AGENT_CONVERSATION_NOT_FOUND"

        row = await _hidden_row(database, conversation_id)
        assert row is not None
        assert row.deletedAt is None

        # The true owner can still delete it afterwards.
        await app_service.delete(identity(OWNER), conversation_id, uuid.uuid4())
        row = await _hidden_row(database, conversation_id)
        assert row is not None
        assert row.deletedAt is not None

    asyncio.run(scenario())


def test_running_conversation_cannot_be_deleted(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        app_service = service(database)
        created = await app_service.create(identity(OWNER), "列出本周高风险项目")
        conversation_id = created.conversation.id
        # create() leaves the durable task QUEUED and the execution RUNNING.

        with pytest.raises(ApiError) as raised:
            await app_service.delete(identity(OWNER), conversation_id, uuid.uuid4())
        assert raised.value.status_code == 409
        assert raised.value.code == "AGENT_CONVERSATION_BUSY"

        # Not hidden: the conversation stays fully usable.
        row = await _hidden_row(database, conversation_id)
        assert row is not None
        assert row.deletedAt is None
        history = await app_service.history(identity(OWNER), conversation_id)
        assert history.runtime is not None
        assert history.runtime.status == "RUNNING"

    asyncio.run(scenario())


def test_waiting_for_user_conversation_cannot_be_deleted(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        app_service = service(database)
        conversation_id = await _seed_waiting_for_user(database, owner=OWNER)

        with pytest.raises(ApiError) as raised:
            await app_service.delete(identity(OWNER), conversation_id, uuid.uuid4())
        assert raised.value.status_code == 409
        assert raised.value.code == "AGENT_CONVERSATION_BUSY"

        row = await _hidden_row(database, conversation_id)
        assert row is not None
        assert row.deletedAt is None

    asyncio.run(scenario())


def test_delete_keeps_the_conversation_on_the_retention_cleanup_path(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A hidden conversation is still selected by the retention expiry sweep.

    The cleanup worker owns the physical lifecycle via ``expiresAt``; the user
    hide marker must not remove (or duplicate) the row from that path.
    """

    async def scenario() -> None:
        app_service = service(database)
        conversation_id = await _seed_terminal_conversation(
            database, owner=OWNER, first_user_message="待保留清理的会话"
        )
        await app_service.delete(identity(OWNER), conversation_id, uuid.uuid4())
        # Expire the conversation, then apply the exact cleanup candidate
        # predicate (retention/cleanup.py: expiresAt <= as_of).
        async with transaction(database) as session:
            await session.execute(
                update(AgentConversation)
                .where(AgentConversation.id == conversation_id)
                .values(expiresAt=datetime.now(UTC) - timedelta(seconds=1))
            )
        async with database() as session:
            candidates = (
                await session.scalars(
                    select(AgentConversation.id).where(
                        AgentConversation.expiresAt <= datetime.now(UTC)
                    )
                )
            ).all()
        assert conversation_id in candidates

    asyncio.run(scenario())


def test_delete_route_is_permission_gated_owner_scoped_and_surfaces_busy(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """DELETE /api/agent/conversations/{id} end-to-end contract."""

    async def scenario() -> None:
        conversation_id = await _seed_terminal_conversation(
            database, owner=OWNER, first_user_message="路由删除测试"
        )
        busy_id = await _seed_waiting_for_user(database, owner=OWNER)
        service_instance = AgentConversationService(database, trace_id=lambda: "t060-trace")

        async def override_identity(
            user_id: UUID = OWNER, *, agent_use: bool = True
        ) -> SessionIdentity:
            return SessionIdentity(
                session_id=uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                user=AuthenticatedUser(
                    id=str(user_id),
                    username="t060",
                    displayName="T060",
                    departmentName=None,
                    roleCodes=["PROJECT_MANAGER"],
                    permissions=["agent.use"] if agent_use else [],
                    dataScope="ALL",
                    mustChangePassword=False,
                ),
            )

        def build_app(identity_override: Callable[..., Any]) -> httpx2.AsyncClient:
            app = create_app(
                Settings(environment="test", cors_origins=("https://web.internal",)),
                AppComposition(
                    routers=(agent_router,),
                    dependency_overrides={
                        current_identity: identity_override,
                        get_agent_service: lambda: service_instance,
                    },
                ),
            )
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
            )

        # Happy path: the owner deletes a terminal conversation over HTTP.
        async with build_app(override_identity) as client:
            response = await client.delete(f"/api/agent/conversations/{conversation_id}")
            assert response.status_code == 200
            assert response.json()["code"] == "OK"

            # A live turn surfaces the busy domain code, not a silent cancel.
            busy = await client.delete(f"/api/agent/conversations/{busy_id}")
            assert busy.status_code == 409
            assert busy.json()["code"] == "AGENT_CONVERSATION_BUSY"

        # Without agent.use the guard rejects before any service work.
        async def identity_no_permission() -> SessionIdentity:
            return await override_identity(OWNER, agent_use=False)

        async def identity_other() -> SessionIdentity:
            return await override_identity(OTHER)

        async with build_app(identity_no_permission) as client:
            response = await client.delete(f"/api/agent/conversations/{busy_id}")
            assert response.status_code == 403
            assert response.json()["code"] == "FORBIDDEN"

        # A foreign owner gets the same 404 as a missing conversation.
        async with build_app(identity_other) as client:
            response = await client.delete(f"/api/agent/conversations/{busy_id}")
            assert response.status_code == 404
            assert response.json()["code"] == "AGENT_CONVERSATION_NOT_FOUND"

    asyncio.run(scenario())
