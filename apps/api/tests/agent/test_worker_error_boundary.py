"""NativeAgentExecutionWorker error-boundary regression.

Only a real ``ProviderError`` may terminalize as ``AGENT_PROVIDER_UNAVAILABLE``.
Domain/tool failures raised through the core (Agent tool argument validation,
scoped query validation, ``WEEKLY_REPORT_STALE``, proposal validation) must
become explicit tool/domain codes — never a provider outage and never the
undifferentiated ``AGENT_INTERNAL_ERROR`` — while exposing only the safe,
server-authored ApiError message/code over SSE.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.core import AgentCoreOutcome, ContextBudget, ReadOnlyAgentCore
from risk_platform.agent.models import (
    AgentConversation,
    AgentEvent,
    AgentEventType,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentMessage,
    AgentMessageRole,
)
from risk_platform.agent.v2_execution import NativeAgentExecutionWorker
from risk_platform.ai_providers.v2_adapter import (
    ProviderError,
    ProviderErrorClassification,
)
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.model_types import JSONValue
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.dispatcher import DurableTaskFailure
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]
OWNER = UUID("00000000-0000-0000-0000-000000000081")


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Agent validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t_errboundary_{uuid.uuid4().hex}"
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
        session.add(
            User(
                id=OWNER,
                username="t-errboundary-owner",
                passwordHash="not-a-real-password-hash",
                displayName="T ErrBoundary Owner",
            )
        )


def _identity() -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(OWNER),
            username="t-errboundary",
            displayName="T ErrBoundary",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=["agent.use", "dashboard.view"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


async def _seed_running_execution(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    async with transaction(factory) as session:
        now = datetime.now(UTC)
        conversation = AgentConversation(
            ownerUserId=OWNER,
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
            content="本周有哪些新增风险？",
            traceId="t-errboundary-trace",
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
                "requested_by_user_id": str(OWNER),
            },
        )
        config_id = uuid4()
        execution = AgentExecution(
            conversationId=conversation.id,
            taskId=task.id,
            userMessageId=message.id,
            requestedByUserId=OWNER,
            status=AgentExecutionStatus.RUNNING,
        )
        session.add(execution)
        session.add(
            AgentExecutionConfig(
                id=config_id,
                taskId=task.id,
                conversationId=conversation.id,
                userMessageId=message.id,
                requestedByUserId=OWNER,
                timeoutSeconds=90,
            )
        )
        await session.flush()
        conversation_id = conversation.id
        execution_id = execution.id
        task_id = task.id
    lease_token = uuid4()
    now = datetime.now(UTC)
    async with transaction(factory) as session:
        result = await session.execute(
            update(DurableTask)
            .where(
                DurableTask.id == task_id,
                DurableTask.status == DurableTaskStatus.QUEUED,
            )
            .values(
                status=DurableTaskStatus.RUNNING,
                leaseToken=lease_token,
                leaseOwner="t-errboundary",
                heartbeatAt=now,
                leaseExpiresAt=now + timedelta(seconds=300),
                attemptCount=DurableTask.attemptCount + 1,
                startedAt=now,
                updatedAt=now,
            )
        )
        assert cast(CursorResult[object], result).rowcount == 1
    return conversation_id, execution_id, task_id, config_id, lease_token


class _RaisingCore:
    """Minimal core double: ``run(identity, message)`` only, raising on call."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.context_budget = ContextBudget()
        self.summarize_conversation = _noop_summary

    async def run(self, identity: SessionIdentity, message: str) -> AgentCoreOutcome:
        del identity, message
        raise self._error


async def _noop_summary(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("summarize_conversation must not be called by the minimal double")


async def _drive(
    factory: async_sessionmaker[AsyncSession], error: Exception
) -> tuple[str, dict[str, JSONValue] | None, AgentExecutionStatus | None]:
    conversation_id, execution_id, task_id, config_id, lease_token = (
        await _seed_running_execution(factory)
    )
    worker = NativeAgentExecutionWorker(
        factory,
        cast(ReadOnlyAgentCore, _RaisingCore(error)),
        heartbeat_interval=15.0,
    )
    failure_code: str | None = None
    try:
        await worker(
            {
                "execution_configuration_id": str(config_id),
                "execution_id": str(execution_id),
                "conversation_id": str(conversation_id),
            },
            task_id=task_id,
            lease_token=lease_token,
        )
    except DurableTaskFailure as failure:
        failure_code = failure.code
    assert failure_code is not None
    async with transaction(factory) as session:
        event = await session.scalar(
            select(AgentEvent)
            .where(AgentEvent.taskId == task_id, AgentEvent.type == AgentEventType.ERROR)
            .order_by(AgentEvent.sequence.desc())
        )
        payload = event.payload if event is not None else None
        execution = await session.get(AgentExecution, execution_id)
    status = execution.status if execution is not None else None
    return failure_code, payload, status


def test_provider_error_maps_to_provider_unavailable(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        code, payload, status = await _drive(
            database,
            ProviderError(
                ProviderErrorClassification.NETWORK,
                retryable=True,
                failover_allowed=True,
            ),
        )
        assert code == "AGENT_PROVIDER_UNAVAILABLE"
        # The dispatcher's finalize pass (after durable exhaustion) owns the
        # terminal provider event; the worker itself must not have written one
        # and must not have terminalized the execution (a RETRY_WAIT attempt
        # cannot leak a terminal SSE error).
        assert payload is None
        assert status is AgentExecutionStatus.RUNNING

    asyncio.run(run())


@pytest.mark.parametrize(
    ("error", "expected_code"),
    (
        (
            ApiError(422, "VALIDATION_ERROR", "Agent 工具参数不符合约束"),
            "AGENT_TOOL_ERROR",
        ),
        (
            ApiError(503, "WEEKLY_REPORT_STALE", "周报汇总正在重建, 请稍后重试"),
            "AGENT_TOOL_ERROR",
        ),
        (
            ApiError(404, "AGENT_INTERACTION_NOT_FOUND", "交互不存在或不属于当前用户"),
            "AGENT_INTERACTION_NOT_FOUND",
        ),
    ),
)
def test_domain_and_tool_errors_never_become_provider_unavailable(
    database: async_sessionmaker[AsyncSession],
    error: ApiError,
    expected_code: str,
) -> None:
    async def run() -> None:
        code, payload, status = await _drive(database, error)
        assert code == expected_code
        assert code != "AGENT_PROVIDER_UNAVAILABLE"
        assert code != "AGENT_INTERNAL_ERROR"
        # The terminal SSE ERROR event carries the explicit code plus the safe
        # server-authored message and detail code — no exception text.
        assert payload is not None
        assert payload["code"] == expected_code
        assert payload["message"] == error.message
        assert payload["detailCode"] == error.code
        assert status is AgentExecutionStatus.FAILED

    asyncio.run(run())


def test_unexpected_exception_is_still_internal_error(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        code, payload, _status = await _drive(database, RuntimeError("boom"))
        assert code == "AGENT_INTERNAL_ERROR"
        # The generic path never leaks the raw exception message.
        assert payload is not None
        assert "message" not in payload
        assert "boom" not in str(payload)

    asyncio.run(run())
