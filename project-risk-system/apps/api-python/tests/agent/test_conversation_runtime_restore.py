"""T052 — owner-scoped conversation runtime restore on refresh/resume.

These PostgreSQL acceptance tests pin the refresh/resume contract introduced by
the conversation-context remediation:

* a RUNNING turn restores a ``streamUrl`` (reattach the same durable execution);
* a WAITING_FOR_USER turn restores the OPEN interaction (project selection or
  write-confirmation draft) instead of forcing a re-send;
* ``continue_conversation`` fails closed with 409 while an execution is active;
* refresh creates no duplicate execution or message;
* an explicit ``POST /cancel`` sets the worker-polled cancel flag, and an
  interaction ``CANCEL`` on a WAITING_FOR_USER turn still cancels.

Transport disconnect (refresh / tab close) no longer cancels the durable
execution — that is covered by ``test_sse_transport_regression.py``.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.events import append_event, open_event_stream
from risk_platform.agent.interaction import AgentInteractionService
from risk_platform.agent.models import (
    AgentConversation,
    AgentEvent,
    AgentEventType,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionAction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    AgentMessageRole,
    MutationDraft,
    MutationDraftOperation,
    MutationDraftStatus,
)
from risk_platform.agent.schemas import AgentInteractionRespondRequest
from risk_platform.agent.service import AgentConversationService
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.risks.models import Risk, RiskCategory, RiskSourceType
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]
OWNER = UUID("00000000-0000-0000-0000-000000000052")
OTHER = UUID("00000000-0000-0000-0000-000000000053")
_PROJECT_CANDIDATE = {
    "id": "00000000-0000-0000-0000-000000000040",
    "name": "南岸项目",
    "externalCode": "P-NAN",
    "departmentName": "工程部",
    "status": "DELIVERY",
}

# Module-level UUIDs for the WRITE_CONFIRMATION seed so ruff's B008
# (no function call in argument defaults) stays satisfied.
_WC_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000060")
_WC_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000061")


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Agent validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t052_{uuid.uuid4().hex}"
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
                    username="t052-owner",
                    passwordHash="not-a-real-password-hash",
                    displayName="T052 Owner",
                ),
                User(
                    id=OTHER,
                    username="t052-other",
                    passwordHash="not-a-real-password-hash",
                    displayName="T052 Other",
                ),
            )
        )


def identity(user_id: UUID = OWNER, permissions: list[str] | None = None) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(user_id),
            username="t052",
            displayName="T052",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=permissions or ["agent.use", "dashboard.view"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


def service(database: async_sessionmaker[AsyncSession]) -> AgentConversationService:
    return AgentConversationService(database, trace_id=lambda: "t052-trace")


async def _seed_waiting_for_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner: UUID = OWNER,
    interaction_type: AgentInteractionType = AgentInteractionType.PROJECT_SELECTION,
    with_draft: bool = False,
    proposal: dict[str, object] | None = None,
) -> tuple[UUID, UUID, UUID]:
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
                id=uuid4(),
                taskId=task.id,
                conversationId=conversation.id,
                userMessageId=message.id,
                requestedByUserId=owner,
                timeoutSeconds=90,
            )
        )
        interaction = AgentInteraction(
            executionId=execution.id,
            conversationId=conversation.id,
            ownerUserId=owner,
            type=interaction_type,
            status=AgentInteractionStatus.OPEN,
            candidateOptions=[_PROJECT_CANDIDATE],
            expiresAt=now + timedelta(minutes=30),
        )
        session.add(interaction)
        await session.flush()
        if with_draft:
            session.add(
                MutationDraft(
                    interactionId=interaction.id,
                    ownerUserId=owner,
                    conversationId=conversation.id,
                    executionId=execution.id,
                    operation=MutationDraftOperation.RISK_CREATE,
                    proposal=proposal
                    or {"title": "新风险", "level": "HIGH"},
                    digest="sha256-test-digest",
                    idempotencyKey=f"draft-{interaction.id}",
                    expiresAt=now + timedelta(minutes=30),
                )
            )
            await session.flush()
        return conversation.id, execution.id, interaction.id


async def _mark_terminal(
    factory: async_sessionmaker[AsyncSession],
    execution_id: UUID,
    *,
    execution_status: AgentExecutionStatus,
    task_status: DurableTaskStatus,
) -> None:
    """Simulate the worker terminalizing a turn (raises DurableTaskCancelled)."""

    async with transaction(factory) as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution is not None
        execution.status = execution_status
        execution.completedAt = datetime.now(UTC)
        task = await session.get(DurableTask, execution.taskId)
        assert task is not None
        task.status = task_status
        task.completedAt = datetime.now(UTC)


def test_running_execution_restores_stream_url_and_is_idempotent(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        # The durable task is QUEUED; the execution is RUNNING → active.
        first = await app_service.history(identity(), created.conversation.id)
        assert first.runtime is not None
        assert first.runtime.status == "RUNNING"
        assert first.runtime.streamUrl == (
            f"/api/agent/conversations/{created.conversation.id}/events"
        )
        assert first.runtime.interaction is None
        # A refresh observes the SAME execution and creates no new message.
        second = await app_service.history(identity(), created.conversation.id)
        assert [m.sequence for m in second.messages] == [
            m.sequence for m in first.messages
        ]
        assert second.runtime is not None
        assert second.runtime.status == "RUNNING"
        async with transaction(database) as session:
            exec_count = await session.scalar(
                select(func.count(AgentExecution.id)).where(
                    AgentExecution.conversationId == created.conversation.id
                )
            )
        assert exec_count == 1

    asyncio.run(run())


def test_waiting_for_user_restores_open_project_selection_interaction(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        conversation_id, _execution_id, interaction_id = await _seed_waiting_for_user(
            database, interaction_type=AgentInteractionType.PROJECT_SELECTION
        )
        history = await app_service.history(identity(), conversation_id)
        assert history.runtime is not None
        assert history.runtime.status == "WAITING_FOR_USER"
        assert history.runtime.streamUrl is None
        interaction = history.runtime.interaction
        assert interaction is not None
        assert interaction.id == interaction_id
        assert interaction.type == "PROJECT_SELECTION"
        assert interaction.status == "OPEN"
        assert interaction.candidates[0]["name"] == "南岸项目"
        assert interaction.draft is None

    asyncio.run(run())


def test_waiting_for_user_restores_open_write_confirmation_with_draft(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        conversation_id, _execution_id, _interaction_id = await _seed_waiting_for_user(
            database,
            interaction_type=AgentInteractionType.WRITE_CONFIRMATION,
            with_draft=True,
        )
        history = await app_service.history(identity(), conversation_id)
        assert history.runtime is not None
        assert history.runtime.status == "WAITING_FOR_USER"
        interaction = history.runtime.interaction
        assert interaction is not None
        assert interaction.type == "WRITE_CONFIRMATION"
        assert interaction.draft is not None
        assert interaction.draft.get("title") == "新风险"

    asyncio.run(run())


def test_continue_conversation_fails_closed_with_409_while_active(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        # RUNNING execution (durable task QUEUED) is still active.
        running = await app_service.create(identity(), "列出本周高风险项目")
        with pytest.raises(ApiError) as running_error:
            await app_service.continue_conversation(
                identity(), running.conversation.id, "继续追问"
            )
        assert running_error.value.status_code == 409
        assert running_error.value.code == "AGENT_EXECUTION_ACTIVE"

        # WAITING_FOR_USER is also active — a new message must not be sent.
        conversation_id, _execution_id, _interaction_id = await _seed_waiting_for_user(database)
        with pytest.raises(ApiError) as waiting_error:
            await app_service.continue_conversation(
                identity(), conversation_id, "继续追问"
            )
        assert waiting_error.value.status_code == 409
        assert waiting_error.value.code == "AGENT_EXECUTION_ACTIVE"

    asyncio.run(run())


def test_refresh_does_not_duplicate_execution_or_messages(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        for _ in range(3):
            history = await app_service.history(identity(), created.conversation.id)
            assert history.runtime is not None
        async with transaction(database) as session:
            exec_count = await session.scalar(
                select(func.count(AgentExecution.id)).where(
                    AgentExecution.conversationId == created.conversation.id
                )
            )
            msg_count = await session.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.conversationId == created.conversation.id
                )
            )
        assert exec_count == 1
        assert msg_count == 1

    asyncio.run(run())


def test_new_conversation_creates_independent_empty_context(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        first = await app_service.create(identity(), "列出本周高风险项目")
        assert first.userMessage.sequence == 1
        # A fresh "新建对话" (no conversation_id) starts an independent context.
        second = await app_service.create(identity(), "另一个项目的风险")
        assert second.conversation.id != first.conversation.id
        assert second.userMessage.sequence == 1
        second_history = await app_service.history(identity(), second.conversation.id)
        assert [m.content for m in second_history.messages] == ["另一个项目的风险"]

    asyncio.run(run())


def test_explicit_cancel_sets_flag_and_interaction_cancel_still_cancels(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        runtime = await app_service.cancel(identity(), created.conversation.id)
        # Pre-worker: the flag is set, the task is still QUEUED → RUNNING runtime.
        assert runtime.status == "RUNNING"
        # The cancel flag is mirrored on the runtime so a restore stays
        # "cancelling" instead of reopening the normal stream while the worker
        # is still RUNNING (ADR 0036 has no CANCELLING status value).
        assert runtime.cancellationRequested is True
        async with transaction(database) as session:
            config = await session.scalar(
                select(AgentExecutionConfig).where(
                    AgentExecutionConfig.conversationId == created.conversation.id
                )
            )
            execution = await session.scalar(
                select(AgentExecution).where(
                    AgentExecution.conversationId == created.conversation.id
                )
            )
        assert config is not None
        assert config.cancellationRequestedAt is not None
        assert execution is not None
        # Simulate the worker observing the flag and terminalizing the turn.
        await _mark_terminal(
            database,
            execution.id,
            execution_status=AgentExecutionStatus.CANCELLED,
            task_status=DurableTaskStatus.CANCELLED,
        )
        terminal = await app_service.history(identity(), created.conversation.id)
        assert terminal.runtime is None

        # An interaction CANCEL on a WAITING_FOR_USER turn cancels directly.
        conversation_id, _execution_id, interaction_id = await _seed_waiting_for_user(database)
        interaction_service = AgentInteractionService(database)
        response = await interaction_service.respond(
            identity(),
            interaction_id,
            AgentInteractionRespondRequest(action="CANCEL"),
            trace_id="t052-trace",
        )
        assert response.interaction.status == "CANCELLED"
        cancelled = await app_service.history(identity(), conversation_id)
        assert cancelled.runtime is None

    asyncio.run(run())


def test_history_runtime_is_owner_scoped(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        # Another owner cannot see — let alone restore — this conversation.
        with pytest.raises(ApiError) as error:
            await app_service.history(identity(OTHER), created.conversation.id)
        assert error.value.code == "AGENT_CONVERSATION_NOT_FOUND"
        with pytest.raises(ApiError) as cancel_error:
            await app_service.cancel(identity(OTHER), created.conversation.id)
        assert cancel_error.value.code == "AGENT_CONVERSATION_NOT_FOUND"

    asyncio.run(run())


def test_running_restore_resumes_from_snapshot_cursor_not_request_time_tail(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """The cursor race fixed by ``resumeAfterEventId``.

    A RUNNING restore must resume the SSE stream from the snapshot event
    cursor, not the request-time tail.  Otherwise, when the worker writes the
    terminal MESSAGE_DELTA + COMPLETED events in the gap between the history
    response and the SSE GET, ``after=None`` re-reads ``lastEventSequence``
    (now the COMPLETED sequence) at GET time, so the stream opens *after* those
    events, observes a terminal task, and closes with no event — the UI goes
    ``disconnected`` and the assistant answer is lost.  Resuming from the
    snapshot cursor replays exactly the events written in that gap.
    """

    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        conversation_id = created.conversation.id
        async with transaction(database) as session:
            execution = await session.scalar(
                select(AgentExecution).where(
                    AgentExecution.conversationId == conversation_id
                )
            )
            assert execution is not None
            task = await session.get(DurableTask, execution.taskId)
            assert task is not None
            execution_id = execution.id
            task_id = task.id
        # Snapshot time: the long-running turn has already written one event (a
        # progress heartbeat), so lastEventSequence > 0 and the runtime records
        # resumeAfterEventId = that event's id.
        async with transaction(database) as session:
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=created.userMessage.id,
                task_id=task_id,
                event_type=AgentEventType.PROGRESS,
                payload={
                    "stage": "thinking",
                    "message": "正在检索风险",
                    "traceId": "t052-trace",
                },
            )
        snapshot = await app_service.history(identity(), conversation_id)
        assert snapshot.runtime is not None
        assert snapshot.runtime.status == "RUNNING"
        assert snapshot.runtime.resumeAfterEventId is not None
        cursor_id = snapshot.runtime.resumeAfterEventId
        # The sequence cursor is always present (here 1, after the single
        # PROGRESS event) and is what the frontend restore actually sends
        # (?afterSequence=<n>); the cancel flag is not set on a live turn yet.
        cursor_sequence = snapshot.runtime.resumeAfterEventSequence
        assert cursor_sequence == 1
        assert snapshot.runtime.cancellationRequested is False
        # Gap: the worker appends the terminal MESSAGE_DELTA + COMPLETED events
        # between the history snapshot and the SSE GET, then terminalizes the
        # execution.  Resuming from the snapshot cursor replays exactly these.
        async with transaction(database) as session:
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=created.userMessage.id,
                task_id=task_id,
                event_type=AgentEventType.MESSAGE_DELTA,
                payload={"text": "共 2 个高风险", "traceId": "t052-trace"},
            )
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=created.userMessage.id,
                task_id=task_id,
                event_type=AgentEventType.COMPLETED,
                payload={"traceId": "t052-trace"},
            )
        await _mark_terminal(
            database,
            execution_id,
            execution_status=AgentExecutionStatus.COMPLETED,
            task_status=DurableTaskStatus.SUCCEEDED,
        )
        # Restore FROM the snapshot cursor: both gap events are replayed and the
        # terminal event closes the stream — the assistant answer is not lost.
        cursor_stream = await open_event_stream(
            database, conversation_id, OWNER, cursor_id, poll_interval=0.001
        )
        cursor_frames = [chunk async for chunk in cursor_stream]
        cursor_joined = b"".join(cursor_frames).decode()
        assert "event: message.delta" in cursor_joined
        assert "event: completed" in cursor_joined
        assert "共 2 个高风险" in cursor_joined
        # The sequence cursor (the one the restore actually sends) replays the
        # same gap events — and it is the only cursor defined for a brand-new
        # turn with zero events, where the event-id cursor is None.
        sequence_stream = await open_event_stream(
            database,
            conversation_id,
            OWNER,
            None,
            after_sequence=cursor_sequence,
            poll_interval=0.001,
        )
        sequence_joined = b"".join([chunk async for chunk in sequence_stream]).decode()
        assert "event: message.delta" in sequence_joined
        assert "event: completed" in sequence_joined
        assert "共 2 个高风险" in sequence_joined
        # The buggy after=None path re-reads the tail at GET time and closes
        # with no event — pin that the snapshot cursor is what avoids the loss.
        buggy_stream = await open_event_stream(
            database, conversation_id, OWNER, None, poll_interval=0.001
        )
        assert [chunk async for chunk in buggy_stream] == []

    asyncio.run(run())


def test_zero_event_running_restore_uses_after_sequence_zero_and_replays_gap(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """The zero-event race closed by ``resumeAfterEventSequence``.

    A brand-new RUNNING turn has written NO AgentEvent yet, so
    ``resumeAfterEventId`` is None and cannot resume.  The runtime still exposes
    ``resumeAfterEventSequence == 0`` (always present), and resuming with
    ``?afterSequence=0`` replays the terminal events the worker writes in the
    REST→SSE gap — the assistant answer is not lost.
    """

    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        conversation_id = created.conversation.id
        async with transaction(database) as session:
            execution = await session.scalar(
                select(AgentExecution).where(
                    AgentExecution.conversationId == conversation_id
                )
            )
            assert execution is not None
            task = await session.get(DurableTask, execution.taskId)
            assert task is not None
            execution_id = execution.id
            task_id = task.id
        # Snapshot BEFORE any event is written: zero events.
        snapshot = await app_service.history(identity(), conversation_id)
        assert snapshot.runtime is not None
        assert snapshot.runtime.status == "RUNNING"
        assert snapshot.runtime.resumeAfterEventId is None
        assert snapshot.runtime.resumeAfterEventSequence == 0
        assert snapshot.runtime.cancellationRequested is False
        # Gap: the worker writes the terminal events.
        async with transaction(database) as session:
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=created.userMessage.id,
                task_id=task_id,
                event_type=AgentEventType.MESSAGE_DELTA,
                payload={"text": "共 2 个高风险", "traceId": "t052-trace"},
            )
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=created.userMessage.id,
                task_id=task_id,
                event_type=AgentEventType.COMPLETED,
                payload={"traceId": "t052-trace"},
            )
        await _mark_terminal(
            database,
            execution_id,
            execution_status=AgentExecutionStatus.COMPLETED,
            task_status=DurableTaskStatus.SUCCEEDED,
        )
        # Restore FROM the zero-event sequence cursor: both gap events replay.
        stream = await open_event_stream(
            database,
            conversation_id,
            OWNER,
            None,
            after_sequence=0,
            poll_interval=0.001,
        )
        joined = b"".join([chunk async for chunk in stream]).decode()
        assert "event: message.delta" in joined
        assert "event: completed" in joined
        assert "共 2 个高风险" in joined

    asyncio.run(run())


def test_after_sequence_beyond_tail_is_unrecoverable(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """An ``afterSequence`` beyond the durable tail fails closed (409).

    A cursor the server can no longer satisfy (snapshot sequence greater than
    the conversation's ``lastEventSequence``) must not silently drop the gap —
    it returns AGENT_EVENT_CURSOR_UNRECOVERABLE so the caller restarts from the
    conversation instead of a stale cursor.
    """

    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        conversation_id = created.conversation.id
        snapshot = await app_service.history(identity(), conversation_id)
        assert snapshot.runtime is not None
        # A brand-new turn: lastEventSequence == 0.
        assert snapshot.runtime.resumeAfterEventSequence == 0
        with pytest.raises(ApiError) as error:
            await open_event_stream(
                database,
                conversation_id,
                OWNER,
                None,
                after_sequence=1,
                poll_interval=0.001,
            )
        assert error.value.status_code == 409
        assert error.value.code == "AGENT_EVENT_CURSOR_UNRECOVERABLE"

    asyncio.run(run())


def test_event_stream_owner_scope_is_404_before_cursor_detail(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A non-owner gets 404 before any cursor detail is leaked.

    The owner-scope check runs before the cursor is read or validated, so a
    caller that does not own the conversation learns nothing about its event
    tail or sequence (not 403, not a cursor error).
    """

    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        conversation_id = created.conversation.id
        # The owner can open the stream (validation passes; the generator is
        # returned without being iterated here).
        await open_event_stream(
            database, conversation_id, OWNER, None, poll_interval=0.001
        )
        with pytest.raises(ApiError) as error:
            await open_event_stream(
                database,
                conversation_id,
                OTHER,
                None,
                after_sequence=0,
                poll_interval=0.001,
            )
        assert error.value.status_code == 404
        assert error.value.code == "AGENT_CONVERSATION_NOT_FOUND"

    asyncio.run(run())


def test_after_and_after_sequence_are_mutually_exclusive(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Both cursors at once is a 422 — the two resume semantics conflict."""

    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        conversation_id = created.conversation.id
        with pytest.raises(ApiError) as error:
            await open_event_stream(
                database,
                conversation_id,
                OWNER,
                uuid4(),  # a bogus event id; the 422 fires before it is read
                after_sequence=0,
                poll_interval=0.001,
            )
        assert error.value.status_code == 422
        assert error.value.code == "VALIDATION_ERROR"

    asyncio.run(run())


def test_create_envelope_sequence_baseline_is_zero(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A brand-new conversation's create envelope carries a zero sequence baseline.

    ``resumeAfterEventSequence`` is ``conversation.lastEventSequence`` snapshotted
    in the transaction that enqueues the durable task, before the worker can see
    it.  A fresh conversation has written no AgentEvent, so the baseline is 0 —
    defined where the event-id cursor would be None and cannot resume.
    """

    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        assert created.resumeAfterEventSequence == 0

    asyncio.run(run())


def test_continue_envelope_sequence_baseline_is_prior_tail(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A continue envelope snapshots the durable tail before the new task.

    After a terminal first turn that wrote two events (PROGRESS + COMPLETED),
    ``lastEventSequence`` is 2.  ``continue_conversation`` snapshots that value
    inside the transaction that enqueues the next task, before it is visible to
    the worker — so the new turn's gap events replay from ``afterSequence=2``
    instead of being lost.
    """

    async def run() -> None:
        app_service = service(database)
        created = await app_service.create(identity(), "列出本周高风险项目")
        assert created.resumeAfterEventSequence == 0
        conversation_id = created.conversation.id
        async with transaction(database) as session:
            execution = await session.scalar(
                select(AgentExecution).where(
                    AgentExecution.conversationId == conversation_id
                )
            )
            assert execution is not None
            task = await session.get(DurableTask, execution.taskId)
            assert task is not None
            execution_id = execution.id
            task_id = task.id
        # First turn writes two events, then terminalizes.
        async with transaction(database) as session:
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=created.userMessage.id,
                task_id=task_id,
                event_type=AgentEventType.PROGRESS,
                payload={"stage": "thinking", "traceId": "t052-trace"},
            )
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=created.userMessage.id,
                task_id=task_id,
                event_type=AgentEventType.COMPLETED,
                payload={"traceId": "t052-trace"},
            )
        await _mark_terminal(
            database,
            execution_id,
            execution_status=AgentExecutionStatus.COMPLETED,
            task_status=DurableTaskStatus.SUCCEEDED,
        )
        # The continue envelope snapshots the tail (2) before enqueuing the
        # new task; creating the USER message does not advance lastEventSequence
        # (only AgentEvent INSERTs do, via the trigger).
        continued = await app_service.continue_conversation(
            identity(), conversation_id, "继续追问"
        )
        assert continued.resumeAfterEventSequence == 2

    asyncio.run(run())


def test_project_selection_respond_envelope_sequence_baseline_replays_gap(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A PROJECT_SELECTION SELECT response snapshots the tail and closes the gap.

    ``respond`` enqueues the resumed task and appends INTERACTION_RESOLVED in the
    same transaction, then snapshots ``lastEventSequence`` (now reflecting the
    RESOLVED event) as ``resumeAfterEventSequence``.  Resuming the stream from
    that baseline replays the resumed execution's terminal events written in the
    POST→SSE gap — the assistant answer is not lost.
    """

    async def run() -> None:
        conversation_id, execution_id, interaction_id = await _seed_waiting_for_user(
            database, interaction_type=AgentInteractionType.PROJECT_SELECTION
        )
        # The SELECT path re-validates the project against the real projects
        # table (and the owner's data scope), so seed the candidate project.
        async with transaction(database) as session:
            session.add(
                Project(
                    id=UUID(_PROJECT_CANDIDATE["id"]),
                    name=str(_PROJECT_CANDIDATE["name"]),
                    externalCode=str(_PROJECT_CANDIDATE["externalCode"]),
                    status=ProjectStatus.DELIVERY,
                    createdAt=datetime.now(UTC),
                    updatedAt=datetime.now(UTC),
                )
            )
        interaction_service = AgentInteractionService(database)
        response = await interaction_service.respond(
            identity(),
            interaction_id,
            AgentInteractionRespondRequest(
                action="SELECT", projectId=UUID(_PROJECT_CANDIDATE["id"])
            ),
            trace_id="t052-trace",
        )
        assert response.streamUrl is not None
        baseline = response.resumeAfterEventSequence
        # INTERACTION_RESOLVED advanced lastEventSequence past 0.
        assert baseline >= 1
        # The resumed execution reuses the same execution row with a new task id.
        async with transaction(database) as session:
            execution = await session.get(AgentExecution, execution_id)
            assert execution is not None
            resumed_task_id = execution.taskId
            message_id = execution.userMessageId
        # The resumed execution writes its terminal events in the POST→SSE gap.
        async with transaction(database) as session:
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=message_id,
                task_id=resumed_task_id,
                event_type=AgentEventType.MESSAGE_DELTA,
                payload={"text": "已选择项目", "traceId": "t052-trace"},
            )
            await append_event(
                session,
                conversation_id=conversation_id,
                message_id=message_id,
                task_id=resumed_task_id,
                event_type=AgentEventType.COMPLETED,
                payload={"traceId": "t052-trace"},
            )
        await _mark_terminal(
            database,
            execution_id,
            execution_status=AgentExecutionStatus.COMPLETED,
            task_status=DurableTaskStatus.SUCCEEDED,
        )
        # Resume from the respond baseline: the gap events replay.
        stream = await open_event_stream(
            database,
            conversation_id,
            OWNER,
            None,
            after_sequence=baseline,
            poll_interval=0.001,
        )
        joined = b"".join([chunk async for chunk in stream]).decode()
        assert "event: message.delta" in joined
        assert "event: completed" in joined
        assert "已选择项目" in joined

    asyncio.run(run())


def _seed_write_confirmation_environment(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner: UUID = OWNER,
    project_id: UUID = _WC_PROJECT_ID,
    category_id: UUID = _WC_CATEGORY_ID,
    project_external_code: str = "P-WC",
    category_code: str = "WC-CAT",
) -> tuple[UUID, UUID, UUID, UUID]:
    """Seed the WRITE_CONFIRMATION commit graph: project + category + draft.

    The real ``_commit`` for RISK_CREATE calls ``RisksService.create_in_session``
    → ``TodosService.ensure_for_risk``, which needs a real ``Project`` row (for
    the data-scope revalidation and the delivery-owner label) and a real active
    ``RiskCategory`` (prevalidation rejects a stale id).  The draft proposal
    carries the full editable field set so CONFIRM exercises the commit path.
    Each caller passes distinct natural keys so the module-scoped shared
    database cannot leak a committed Risk from a sibling test into the
    ``risk_count`` assertion.
    """

    async def seed() -> tuple[UUID, UUID, UUID, UUID]:
        async with transaction(factory) as session:
            now = datetime.now(UTC)
            existing_project = await session.get(Project, project_id)
            if existing_project is None:
                session.add(
                    Project(
                        id=project_id,
                        name="写确认项目",
                        externalCode=project_external_code,
                        status=ProjectStatus.DELIVERY,
                        deliveryOwnerName="负责人",
                        createdAt=now,
                        updatedAt=now,
                    )
                )
            existing_category = await session.get(RiskCategory, category_id)
            if existing_category is None:
                session.add(
                    RiskCategory(
                        id=category_id,
                        code=category_code,
                        name="写确认分类",
                        sortOrder=0,
                        isActive=True,
                        createdAt=now,
                        updatedAt=now,
                    )
                )
            await session.flush()
        (
            conversation_id,
            execution_id,
            interaction_id,
        ) = await _seed_waiting_for_user(
            factory,
            owner=owner,
            interaction_type=AgentInteractionType.WRITE_CONFIRMATION,
            with_draft=True,
            proposal={
                "projectId": str(project_id),
                "category": str(category_id),
                "title": "写确认高风险",
                "description": "用于终端化的写确认风险描述",
                "level": "HIGH",
                "evidence": "证据",
                "suggestion": "建议",
            },
        )
        return conversation_id, execution_id, interaction_id, project_id

    return seed()  # type: ignore[return-value]


def test_write_confirmation_confirm_terminalizes_execution_and_continues(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A WRITE_CONFIRMATION CONFIRM commits the draft and ends the turn atomically.

    After CONFIRM the interaction is RESOLVED, the draft is CONFIRMED, the
    execution is COMPLETED with ``completedAt`` set, an INTERACTION_RESOLVED
    event was appended, and ``history.runtime is None`` (a refresh sees a
    terminal turn, not a dangling WAITING_FOR_USER).  A subsequent
    ``continue_conversation`` no longer raises ``AGENT_EXECUTION_ACTIVE`` —
    the terminalized turn releases the conversation.
    """

    async def run() -> None:
        app_service = service(database)
        (
            conversation_id,
            execution_id,
            interaction_id,
            project_id,
        ) = await _seed_write_confirmation_environment(
            database,
            project_id=UUID("00000000-0000-0000-0000-000000000062"),
            category_id=UUID("00000000-0000-0000-0000-000000000072"),
            project_external_code="P-WC-CONFIRM",
            category_code="WC-CONFIRM",
        )

        interaction_service = AgentInteractionService(database)
        response = await interaction_service.respond(
            identity(permissions=["agent.use", "dashboard.view", "risk.report"]),
            interaction_id,
            AgentInteractionRespondRequest(
                action="CONFIRM", finalFields={"title": "写确认高风险-确认"}
            ),
            trace_id="00000000-0000-4000-8000-000000000070",
        )
        # The CONFIRM path starts no new durable task: the turn is terminal.
        assert response.streamUrl is None
        assert response.resumeAfterEventSequence == 0
        assert response.interaction.status == "RESOLVED"
        assert response.interaction.type == "WRITE_CONFIRMATION"

        async with transaction(database) as session:
            interaction = await session.get(AgentInteraction, interaction_id)
            assert interaction is not None
            assert interaction.status is AgentInteractionStatus.RESOLVED
            assert interaction.responseAction is AgentInteractionAction.CONFIRM
            draft = await session.scalar(
                select(MutationDraft).where(
                    MutationDraft.interactionId == interaction_id
                )
            )
            assert draft is not None
            assert draft.status is MutationDraftStatus.CONFIRMED
            execution = await session.get(AgentExecution, execution_id)
            assert execution is not None
            assert execution.status is AgentExecutionStatus.COMPLETED
            assert execution.completedAt is not None
            resolved_event = await session.scalar(
                select(AgentEvent.type).where(
                    AgentEvent.taskId == execution.taskId,
                    AgentEvent.type == AgentEventType.INTERACTION_RESOLVED,
                )
            )
            assert resolved_event is AgentEventType.INTERACTION_RESOLVED
            risk = await session.scalar(
                select(Risk).where(Risk.projectId == project_id)
            )
            assert risk is not None
            assert risk.title == "写确认高风险-确认"

        history = await app_service.history(identity(), conversation_id)
        assert history.runtime is None
        # The terminalized turn no longer blocks a new message.
        continued = await app_service.continue_conversation(
            identity(), conversation_id, "继续追问"
        )
        assert continued.userMessage.content == "继续追问"

    asyncio.run(run())


def test_write_confirmation_cancel_terminalizes_execution_and_continues(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A WRITE_CONFIRMATION CANCEL aborts the draft and ends the turn atomically.

    After CANCEL the interaction is CANCELLED, the draft is CANCELLED, the
    execution is CANCELLED with ``completedAt`` set, no Risk was committed, an
    INTERACTION_RESOLVED event was appended, ``history.runtime is None``, and
    a subsequent ``continue_conversation`` no longer raises
    ``AGENT_EXECUTION_ACTIVE``.
    """

    async def run() -> None:
        app_service = service(database)
        (
            conversation_id,
            execution_id,
            interaction_id,
            project_id,
        ) = await _seed_write_confirmation_environment(
            database,
            project_id=UUID("00000000-0000-0000-0000-000000000063"),
            category_id=UUID("00000000-0000-0000-0000-000000000073"),
            project_external_code="P-WC-CANCEL",
            category_code="WC-CANCEL",
        )

        interaction_service = AgentInteractionService(database)
        response = await interaction_service.respond(
            identity(),
            interaction_id,
            AgentInteractionRespondRequest(action="CANCEL"),
            trace_id="00000000-0000-4000-8000-000000000070",
        )
        assert response.streamUrl is None
        assert response.resumeAfterEventSequence == 0
        assert response.interaction.status == "CANCELLED"

        async with transaction(database) as session:
            interaction = await session.get(AgentInteraction, interaction_id)
            assert interaction is not None
            assert interaction.status is AgentInteractionStatus.CANCELLED
            assert interaction.responseAction is AgentInteractionAction.CANCEL
            draft = await session.scalar(
                select(MutationDraft).where(
                    MutationDraft.interactionId == interaction_id
                )
            )
            assert draft is not None
            assert draft.status is MutationDraftStatus.CANCELLED
            execution = await session.get(AgentExecution, execution_id)
            assert execution is not None
            assert execution.status is AgentExecutionStatus.CANCELLED
            assert execution.completedAt is not None
            resolved_event = await session.scalar(
                select(AgentEvent.type).where(
                    AgentEvent.taskId == execution.taskId,
                    AgentEvent.type == AgentEventType.INTERACTION_RESOLVED,
                )
            )
            assert resolved_event is AgentEventType.INTERACTION_RESOLVED
            # No Risk was committed: CANCEL aborts before ``_commit`` runs.
            risk_count = await session.scalar(
                select(func.count(Risk.id))
                .where(Risk.projectId == project_id)
                .where(Risk.sourceType == RiskSourceType.AGENT)
            )
            assert risk_count == 0

        history = await app_service.history(identity(), conversation_id)
        assert history.runtime is None
        continued = await app_service.continue_conversation(
            identity(), conversation_id, "继续追问"
        )
        assert continued.userMessage.content == "继续追问"

    asyncio.run(run())
