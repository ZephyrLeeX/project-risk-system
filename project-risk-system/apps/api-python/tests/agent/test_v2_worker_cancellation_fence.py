"""T052 — post-core cancellation fence (Finding 2).

The ``NativeAgentExecutionWorker`` observed the cancel flag only at its heartbeat
boundary.  If the core completed between heartbeats, a cancel that landed during
the core run could be written to the flag but the worker still proceeded through
``_complete`` (writing a normal COMPLETED assistant), ``_wait_for_project_selection``
(creating an OPEN PROJECT_SELECTION), or the WRITE_CONFIRMATION path (leaving an
OPEN draft).  These PostgreSQL race tests pin the post-core fence added to each
of those state transitions:

* cancel + ``ProjectSelectionRequired`` → no OPEN interaction, execution CANCELLED;
* cancel + a write proposal (``MutationConfirmationRequired``) → no OPEN draft,
  execution CANCELLED;
* cancel + a normal completion → no COMPLETED assistant, execution CANCELLED.

The fake core sets the cancel flag *inside* ``run`` (a separate transaction) and
then returns/raises, so only the post-core fence — not the heartbeat boundary —
observes it.  That is the exact race the fence closes.
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
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.core import (
    AgentCoreOutcome,
    ContextBudget,
    ProjectSelectionRequired,
    ReadOnlyAgentCore,
)
from risk_platform.agent.models import (
    AgentConversation,
    AgentEvent,
    AgentEventType,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    AgentMessageRole,
    MutationDraft,
    MutationDraftOperation,
    MutationDraftStatus,
)
from risk_platform.agent.mutations import MutationConfirmationRequired
from risk_platform.agent.v2_execution import NativeAgentExecutionWorker
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.model_types import JSONValue
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.dispatcher import DurableTaskCancelled
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus

ROOT = Path(__file__).resolve().parents[2]
OWNER = UUID("00000000-0000-0000-0000-000000000061")
_PROJECT_CANDIDATE: dict[str, JSONValue] = {
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
    schema = f"t052fence_{uuid.uuid4().hex}"
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
                username="t052fence-owner",
                passwordHash="not-a-real-password-hash",
                displayName="T052 Fence Owner",
            )
        )


def identity() -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(OWNER),
            username="t052fence",
            displayName="T052 Fence",
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
    """Seed a conversation whose execution is RUNNING on a claimed durable task.

    No worker has run yet; the cancel flag is unset so ``_load`` proceeds.  The
    returned lease token is the one ``_load`` requires to match.
    """

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
            content="查询本周风险",
            traceId="t052-trace",
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
    # Claim the task → RUNNING with a known lease token (``_load`` requires the
    # task to be RUNNING and the lease token to match).
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
                leaseOwner="t052fence",
                heartbeatAt=now,
                leaseExpiresAt=now + timedelta(seconds=300),
                attemptCount=DurableTask.attemptCount + 1,
                startedAt=now,
                updatedAt=now,
            )
        )
        assert cast(CursorResult[object], result).rowcount == 1
    return conversation_id, execution_id, task_id, config_id, lease_token


async def _set_cancel_flag(
    factory: async_sessionmaker[AsyncSession], config_id: UUID
) -> None:
    """Set the worker-polled cancel flag (a POST /cancel that arrived mid-run)."""

    async with transaction(factory) as session:
        config = await session.get(AgentExecutionConfig, config_id)
        assert config is not None
        if config.cancellationRequestedAt is None:
            config.cancellationRequestedAt = datetime.now(UTC)


async def _create_open_write_confirmation(
    factory: async_sessionmaker[AsyncSession], config_id: UUID
) -> None:
    """Simulate the core's pre-raise setup for a write proposal.

    The real core creates the OPEN WRITE_CONFIRMATION interaction + draft and
    moves the execution to WAITING_FOR_USER inside its own transaction BEFORE
    raising ``MutationConfirmationRequired``.  The fence then abandons that
    proposal when a cancel arrived while the core was proposing.
    """

    async with transaction(factory) as session:
        config = await session.get(AgentExecutionConfig, config_id)
        assert config is not None
        execution = await session.scalar(
            select(AgentExecution)
            .where(AgentExecution.taskId == config.taskId)
            .with_for_update()
        )
        assert execution is not None
        now = datetime.now(UTC)
        interaction = AgentInteraction(
            executionId=execution.id,
            conversationId=execution.conversationId,
            ownerUserId=execution.requestedByUserId,
            type=AgentInteractionType.WRITE_CONFIRMATION,
            status=AgentInteractionStatus.OPEN,
            candidateOptions=[],
            expiresAt=now + timedelta(minutes=30),
        )
        session.add(interaction)
        await session.flush()
        session.add(
            MutationDraft(
                interactionId=interaction.id,
                ownerUserId=execution.requestedByUserId,
                conversationId=execution.conversationId,
                executionId=execution.id,
                operation=MutationDraftOperation.RISK_CREATE,
                proposal={"title": "新风险", "level": "HIGH"},
                digest="sha256-test-digest",
                idempotencyKey=f"draft-{interaction.id}",
                expiresAt=now + timedelta(minutes=30),
            )
        )
        execution.status = AgentExecutionStatus.WAITING_FOR_USER
        execution.updatedAt = now
        await session.flush()


async def _noop_summary(*_args: object, **_kwargs: object) -> str:
    """Placeholder summarizer; never invoked for the minimal core double."""

    raise AssertionError("summarize_conversation must not be called by the minimal double")


class _FakeCore:
    """Minimal ``ReadOnlyAgentCore`` double that drives the post-core cancel fence.

    Declares only ``run(identity, message)`` so ``_invoke_core`` takes the
    early-return path and never builds a conversation context.  The cancel flag
    is set *inside* ``run`` (a separate transaction) and the core then
    returns/raises — so only the post-core fence, not the heartbeat boundary,
    observes the cancel.  That is the exact race Finding 2 closes.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        config_id: UUID,
        behavior: str,
    ) -> None:
        self._sessions = sessions
        self._config_id = config_id
        self._behavior = behavior
        self.context_budget = ContextBudget()
        # Stored by the worker constructor; never invoked for the minimal run
        # signature (the early-return path skips conversation-context building).
        self.summarize_conversation = _noop_summary

    async def run(self, identity: SessionIdentity, message: str) -> AgentCoreOutcome:
        del identity, message
        # A cancel arrives mid-execution: set the worker-polled flag BEFORE the
        # core returns/raises, so only the post-core fence (not the heartbeat
        # boundary the worker already passed) can observe it.
        await _set_cancel_flag(self._sessions, self._config_id)
        if self._behavior == "complete":
            return AgentCoreOutcome(text="答复")
        if self._behavior == "project_selection":
            raise ProjectSelectionRequired(candidates=(_PROJECT_CANDIDATE,))
        if self._behavior == "write_confirmation":
            await _create_open_write_confirmation(self._sessions, self._config_id)
            raise MutationConfirmationRequired
        raise AssertionError(f"unknown behavior {self._behavior!r}")


def _worker(
    database: async_sessionmaker[AsyncSession], config_id: UUID, behavior: str
) -> NativeAgentExecutionWorker:
    return NativeAgentExecutionWorker(
        database,
        cast(ReadOnlyAgentCore, _FakeCore(database, config_id, behavior)),
        heartbeat_interval=15.0,
    )


def test_cancel_racing_project_selection_creates_no_open_interaction_and_cancels(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        conversation_id, execution_id, task_id, config_id, lease_token = (
            await _seed_running_execution(database)
        )
        worker = _worker(database, config_id, "project_selection")
        with pytest.raises(DurableTaskCancelled):
            await worker(
                {
                    "execution_configuration_id": str(config_id),
                    "execution_id": str(execution_id),
                    "conversation_id": str(conversation_id),
                },
                task_id=task_id,
                lease_token=lease_token,
            )
        async with transaction(database) as session:
            open_interactions = await session.scalar(
                select(func.count(AgentInteraction.id)).where(
                    AgentInteraction.executionId == execution_id,
                    AgentInteraction.status == AgentInteractionStatus.OPEN,
                )
            )
            all_interactions = await session.scalar(
                select(func.count(AgentInteraction.id)).where(
                    AgentInteraction.executionId == execution_id
                )
            )
            execution = await session.get(AgentExecution, execution_id)
        assert open_interactions == 0
        # No interaction was created at all — the fence raised before it.
        assert all_interactions == 0
        assert execution is not None
        assert execution.status is AgentExecutionStatus.CANCELLED

    asyncio.run(run())


def test_cancel_racing_write_confirmation_abandons_open_draft_and_cancels(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        conversation_id, execution_id, task_id, config_id, lease_token = (
            await _seed_running_execution(database)
        )
        worker = _worker(database, config_id, "write_confirmation")
        with pytest.raises(DurableTaskCancelled):
            await worker(
                {
                    "execution_configuration_id": str(config_id),
                    "execution_id": str(execution_id),
                    "conversation_id": str(conversation_id),
                },
                task_id=task_id,
                lease_token=lease_token,
            )
        async with transaction(database) as session:
            open_interactions = await session.scalar(
                select(func.count(AgentInteraction.id)).where(
                    AgentInteraction.executionId == execution_id,
                    AgentInteraction.status == AgentInteractionStatus.OPEN,
                )
            )
            interaction = await session.scalar(
                select(AgentInteraction).where(
                    AgentInteraction.executionId == execution_id
                )
            )
            draft = None
            if interaction is not None:
                draft = await session.scalar(
                    select(MutationDraft).where(
                        MutationDraft.interactionId == interaction.id
                    )
                )
            execution = await session.get(AgentExecution, execution_id)
        assert open_interactions == 0
        assert interaction is not None
        assert interaction.status is AgentInteractionStatus.CANCELLED
        assert interaction.responseAction is not None
        assert draft is not None
        assert draft.status is MutationDraftStatus.CANCELLED
        assert execution is not None
        assert execution.status is AgentExecutionStatus.CANCELLED

    asyncio.run(run())


def test_cancel_racing_normal_completion_writes_no_assistant_and_cancels(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        conversation_id, execution_id, task_id, config_id, lease_token = (
            await _seed_running_execution(database)
        )
        worker = _worker(database, config_id, "complete")
        with pytest.raises(DurableTaskCancelled):
            await worker(
                {
                    "execution_configuration_id": str(config_id),
                    "execution_id": str(execution_id),
                    "conversation_id": str(conversation_id),
                },
                task_id=task_id,
                lease_token=lease_token,
            )
        async with transaction(database) as session:
            assistant_count = await session.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.conversationId == conversation_id,
                    AgentMessage.role == AgentMessageRole.ASSISTANT,
                )
            )
            completed_events = await session.scalar(
                select(func.count(AgentEvent.id)).where(
                    AgentEvent.taskId == task_id,
                    AgentEvent.type == AgentEventType.COMPLETED,
                )
            )
            execution = await session.get(AgentExecution, execution_id)
        assert assistant_count == 0
        assert completed_events == 0
        assert execution is not None
        assert execution.status is AgentExecutionStatus.CANCELLED

    asyncio.run(run())


def test_no_cancel_lets_project_selection_proceed_normally(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Control: without a mid-run cancel the fence does not fire.

    A core that raises ``ProjectSelectionRequired`` without first setting the
    cancel flag must still create the OPEN interaction and pause the execution —
    the fence only aborts when the flag is actually set, so a normal turn is not
    falsely cancelled.
    """

    async def run() -> None:
        _conversation_id, execution_id, task_id, config_id, lease_token = (
            await _seed_running_execution(database)
        )
        # A core that raises without setting the cancel flag.
        core = cast(ReadOnlyAgentCore, _NoCancelProjectSelectionCore())
        worker = NativeAgentExecutionWorker(database, core, heartbeat_interval=15.0)
        await worker(
            {"execution_configuration_id": str(config_id), "execution_id": str(execution_id)},
            task_id=task_id,
            lease_token=lease_token,
        )
        async with transaction(database) as session:
            execution = await session.get(AgentExecution, execution_id)
            interaction = await session.scalar(
                select(AgentInteraction).where(
                    AgentInteraction.executionId == execution_id,
                    AgentInteraction.status == AgentInteractionStatus.OPEN,
                )
            )
        assert execution is not None
        assert execution.status is AgentExecutionStatus.WAITING_FOR_USER
        assert interaction is not None
        assert interaction.type is AgentInteractionType.PROJECT_SELECTION

    asyncio.run(run())


class _NoCancelProjectSelectionCore:
    """Raises ``ProjectSelectionRequired`` without touching the cancel flag."""

    context_budget = ContextBudget()
    summarize_conversation = _noop_summary

    async def run(self, identity: SessionIdentity, message: str) -> AgentCoreOutcome:
        del identity, message
        raise ProjectSelectionRequired(candidates=(_PROJECT_CANDIDATE,))
