from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from celery import Celery
from celery.contrib.testing.worker import start_worker  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.agent.api import get_agent_service, router
from risk_platform.agent.events import append_event, open_event_stream, request_cancellation
from risk_platform.agent.execution import (
    AgentExecutionWorker,
    AgentProviderError,
    AgentProviderInvalidOutput,
    PreviewAction,
    ProviderTransportResponse,
    agent_execution_handlers,
)
from risk_platform.agent.models import (
    AgentConfirmationToken,
    AgentEvent,
    AgentEventType,
    AgentExecutionConfig,
)
from risk_platform.agent.schemas import AgentToolResult
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.ai_providers.models import AiConnectionStatus, AiProviderConfig
from risk_platform.app import AppComposition, create_app
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType, Role, UserRole
from risk_platform.reliability.celery_app import create_celery_app
from risk_platform.reliability.core import TaskHandler
from risk_platform.reliability.dispatcher import register_executor
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.risks.models import Risk
from risk_platform.risks.service import RisksService
from risk_platform.seed import SeedSettings, seed_reference_data
from risk_platform.shared.errors import ApiError
from risk_platform.todos.models import ActionItem
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import WeeklyReportService

ROOT = Path(__file__).resolve().parents[2]
OWNER = UUID("00000000-0000-0000-0000-000000000129")


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[Mapping[str, object]] = []

    async def __call__(
        self, config: AgentExecutionConfig, request: dict[str, JSONValue]
    ) -> ProviderTransportResponse:
        del config
        self.requests.append(request)
        message = str(request["message"])
        phase = request["phase"]
        if message == "invalid":
            return self._response({"protocol": "wrong", "phase": phase, "actions": []})
        if message == "timeout":
            raise TimeoutError
        if message == "heartbeat":
            await asyncio.sleep(0.12)
        if phase == "PLAN":
            actions: list[JSONValue] = [
                {"type": "progress", "stage": "analyzing", "message": "正在分析"}
            ]
            if message in {"tool", "slow-tool", "cancel-tool", "cadence-tools"}:
                count = 8 if message == "cadence-tools" else 1
                actions.extend(
                    {"type": "tool_call", "name": "risk_list", "arguments": {}}
                    for _ in range(count)
                )
            return self._response(
                {"protocol": "AGENT_PROVIDER_EXECUTION_V1", "phase": phase, "actions": actions}
            )
        response_actions: list[JSONValue] = [{"type": "text_delta", "text": "已完成分析"}]
        if message == "preview":
            response_actions.append(
                {
                    "type": "preview_proposal",
                    "operation": "REPORT",
                    "content": {
                        "operation": "REPORT",
                        "projectId": str(PROJECT),
                        "riskId": None,
                        "todoId": None,
                        "title": "交付风险",
                        "description": "里程碑可能延期",
                        "riskLevel": "HIGH",
                        "dueDate": None,
                        "assigneeUserId": None,
                    },
                }
            )
        return self._response(
            {
                "protocol": "AGENT_PROVIDER_EXECUTION_V1",
                "phase": phase,
                "actions": response_actions,
            }
        )

    @staticmethod
    def _response(value: object) -> ProviderTransportResponse:
        return ProviderTransportResponse(
            200,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
        )


PROJECT = UUID("00000000-0000-0000-0000-000000000130")


class SlowTools:
    def __init__(self, delay: float = 0.2) -> None:
        self._delay = delay

    def catalogue(self, value: SessionIdentity) -> list[dict[str, object]]:
        del value
        return [{"name": "risk_list", "description": "slow", "argumentsSchema": {}}]

    async def invoke(
        self,
        value: SessionIdentity,
        name: str,
        arguments: Mapping[str, object],
        *,
        trace_id: str,
    ) -> AgentToolResult:
        del value, name, arguments
        await asyncio.sleep(self._delay)
        return AgentToolResult(
            tool="risk_list", data={}, dataAsOf=datetime.now(UTC), traceId=trace_id
        )


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; T029 PostgreSQL acceptance 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t029_{uuid.uuid4().hex}"
    admin = create_engine(sync_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    with migration.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration.dispose()
    engine = create_database_engine(f"{sync_url}?options=-csearch_path%3D{schema}")
    factory = create_session_factory(engine)
    asyncio.run(_seed(factory))
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(factory) as session:
        await seed_reference_data(
            session,
            SeedSettings("t029-admin", "T029 Admin", "T029-Strong!Password9", 12),
        )
        role = await session.scalar(select(Role).where(Role.code == "RISK_ADMIN"))
        assert role is not None
        owner = User(
            id=OWNER,
            username="t029-owner",
            passwordHash="not-used",
            displayName="T029 Owner",
            mustChangePassword=False,
        )
        session.add(owner)
        await session.flush()
        session.add(UserRole(userId=OWNER, roleId=role.id, dataScope=DataScopeType.ALL))
        session.add(Project(id=PROJECT, name="T029 Project", managerId=OWNER))
        session.add(
            AiProviderConfig(
                name="t029-provider",
                vendor="test",
                endpoint="https://provider.example.test/v1",
                model="fake-model",
                encryptedApiKey="encrypted-only",
                keyIv="unused-iv",
                keyAuthTag="unused-tag",
                keyLast4="test",
                enabled=True,
                isDefault=True,
                lastTestStatus=AiConnectionStatus.HEALTHY,
            )
        )


def identity() -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(OWNER),
            username="t029-owner",
            displayName="T029 Owner",
            departmentName=None,
            roleCodes=["RISK_ADMIN"],
            permissions=["agent.use", "dashboard.view", "risk.report", "risk.resolve"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


def tools(factory: async_sessionmaker[AsyncSession]) -> AgentToolRegistry:
    return AgentToolRegistry(
        DashboardService(factory),
        RisksService(factory),
        TodosService(factory),
        WeeklyReportService(factory),
    )


@contextmanager
def real_worker(
    factory: async_sessionmaker[AsyncSession],
    provider: FakeProvider,
    *,
    heartbeat_interval: float | None = None,
    attempt_timeout_seconds: float | None = None,
    registry: AgentToolRegistry | None = None,
) -> Iterator[Celery]:
    celery = create_celery_app()
    queue = f"t029-worker-{uuid.uuid4().hex}"
    celery.conf.update(
        task_default_queue=queue,
        task_default_exchange=queue,
        task_default_routing_key=queue,
    )
    selected_tools = registry or tools(factory)
    handlers = (
        agent_execution_handlers(factory, provider, selected_tools)
        if heartbeat_interval is None and attempt_timeout_seconds is None
        else {
            DurableTaskKind.AGENT_EXECUTION.value: cast(
                TaskHandler,
                AgentExecutionWorker(
                    factory,
                    provider,
                    selected_tools,
                    heartbeat_interval=heartbeat_interval or 15.0,
                    attempt_timeout_seconds=attempt_timeout_seconds,
                ),
            )
        }
    )
    assert tuple(handlers) == (DurableTaskKind.AGENT_EXECUTION.value,)
    register_executor(celery, factory, handlers, owner=queue)
    with start_worker(
        celery,
        pool="solo",
        concurrency=1,
        queues=[queue],
        perform_ping_check=False,
        loglevel="WARNING",
    ):
        yield celery


async def _created(
    factory: async_sessionmaker[AsyncSession], message: str
) -> tuple[UUID, UUID]:
    result = await AgentConversationService(factory, trace_id=lambda: "t029-trace").create(
        identity(), message
    )
    async with factory() as session:
        config = await session.scalar(
            select(AgentExecutionConfig).where(
                AgentExecutionConfig.userMessageId == result.userMessage.id
            )
        )
        assert config is not None
        return result.conversation.id, config.taskId


async def _wait(
    factory: async_sessionmaker[AsyncSession], task_id: UUID, statuses: set[DurableTaskStatus]
) -> DurableTask:
    last: DurableTask | None = None
    for _ in range(200):
        async with factory() as session:
            task = await session.get(DurableTask, task_id)
            last = task
            if task is not None and task.status in statuses:
                return task
        await asyncio.sleep(0.05)
    detail = None if last is None else (last.status.value, last.failureCode, last.failureSummary)
    raise AssertionError(f"real Celery worker did not reach the expected durable state: {detail}")


def test_protocol_models_reject_unknown_and_mismatched_preview_fields() -> None:
    with pytest.raises(AgentProviderInvalidOutput):
        AgentExecutionWorker._validate_response(
            {
                "protocol": "AGENT_PROVIDER_EXECUTION_V1",
                "phase": "PLAN",
                "actions": [{"type": "text_delta", "text": "not allowed"}],
            },
            "PLAN",
        )
    too_many_calls: list[JSONValue] = [
        {"type": "tool_call", "name": "risk_list", "arguments": {}} for _ in range(9)
    ]
    with pytest.raises(AgentProviderInvalidOutput):
        AgentExecutionWorker._validate_response(
            {
                "protocol": "AGENT_PROVIDER_EXECUTION_V1",
                "phase": "PLAN",
                "actions": too_many_calls,
            },
            "PLAN",
        )
    invalid_transports = (
        ProviderTransportResponse(200, b"\xff"),
        ProviderTransportResponse(200, b"[]"),
        ProviderTransportResponse(200, b'{"phase":"PLAN","phase":"RESPOND"}'),
        ProviderTransportResponse(200, b"{" + b" " * (128 * 1024) + b"}"),
    )
    for response in invalid_transports:
        with pytest.raises(AgentProviderInvalidOutput):
            AgentExecutionWorker._parse_transport(response)
    with pytest.raises(AgentProviderError) as rejected:
        AgentExecutionWorker._parse_transport(ProviderTransportResponse(400, b"{}"))
    assert not rejected.value.retryable
    with pytest.raises(AgentProviderError) as unavailable:
        AgentExecutionWorker._parse_transport(ProviderTransportResponse(429, b"{}"))
    assert unavailable.value.retryable
    with pytest.raises(ValidationError):
        PreviewAction.model_validate(
            {
                "type": "preview_proposal",
                "operation": "RESOLVE",
                "content": {
                    "operation": "REPORT",
                    "projectId": str(PROJECT),
                    "riskId": None,
                    "todoId": None,
                    "title": "x",
                    "description": "y",
                    "riskLevel": "HIGH",
                    "dueDate": None,
                    "assigneeUserId": None,
                    "confirmationToken": "smuggled",
                },
            }
        )

    async def invalid_tool_arguments() -> None:
        with pytest.raises(ApiError) as error:
            await tools(cast(async_sessionmaker[AsyncSession], None)).invoke(
                identity(), "risk_list", {"unknown": True}, trace_id="trace"
            )
        assert error.value.code == "VALIDATION_ERROR"

    asyncio.run(invalid_tool_arguments())

    invalid_preview_contents = (
        {
            "operation": "PROCESS",
            "projectId": str(PROJECT),
            "riskId": str(uuid.uuid4()),
            "todoId": str(uuid.uuid4()),
            "title": "must-not-change-risk-title",
            "description": "处理",
            "riskLevel": None,
            "dueDate": None,
            "assigneeUserId": None,
        },
        {
            "operation": "RESOLVE",
            "projectId": str(PROJECT),
            "riskId": str(uuid.uuid4()),
            "todoId": str(uuid.uuid4()),
            "title": "",
            "description": "解除",
            "riskLevel": None,
            "dueDate": None,
            "assigneeUserId": None,
        },
    )
    for content in invalid_preview_contents:
        with pytest.raises(ValidationError):
            PreviewAction.model_validate(
                {
                    "type": "preview_proposal",
                    "operation": content["operation"],
                    "content": content,
                }
            )


def test_real_module_local_worker_success_preview_invalid_timeout_and_cancellation(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run(celery: Celery) -> None:
        before_risks: int
        async with database() as session:
            before_risks = int(await session.scalar(select(func.count(Risk.id))) or 0)
        success_conversation, success_task = await _created(database, "success")
        duplicate = await AgentConversationService(database).continue_conversation(
            identity(), success_conversation, "must-not-create-a-second-message"
        )
        async with database() as session:
            duplicate_config = await session.scalar(
                select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == success_task)
            )
            assert duplicate_config is not None
            assert duplicate.userMessage.id == duplicate_config.userMessageId
        celery.send_task("risk_platform.reliability.execute", args=[str(success_task), 1])
        success = await _wait(database, success_task, {DurableTaskStatus.SUCCEEDED})
        assert success.attemptCount == 1
        provider_calls = len(provider.requests)
        async with database.begin() as session:
            recovered = await session.get(DurableTask, success_task)
            assert recovered is not None
            recovered.status = DurableTaskStatus.QUEUED
            recovered.dispatchGeneration = 2
            recovered.completedAt = None
        celery.send_task("risk_platform.reliability.execute", args=[str(success_task), 2])
        recovered = await _wait(database, success_task, {DurableTaskStatus.SUCCEEDED})
        assert recovered.attemptCount == 2
        assert len(provider.requests) == provider_calls

        _tool_conversation, tool_task = await _created(database, "tool")
        celery.send_task("risk_platform.reliability.execute", args=[str(tool_task), 1])
        await _wait(database, tool_task, {DurableTaskStatus.SUCCEEDED})

        preview_conversation, preview_task = await _created(database, "preview")
        celery.send_task("risk_platform.reliability.execute", args=[str(preview_task), 1])
        await _wait(database, preview_task, {DurableTaskStatus.SUCCEEDED})

        _invalid_conversation, invalid_task = await _created(database, "invalid")
        celery.send_task("risk_platform.reliability.execute", args=[str(invalid_task), 1])
        invalid = await _wait(database, invalid_task, {DurableTaskStatus.FAILED})
        assert invalid.failureCode == "AGENT_PROVIDER_INVALID_OUTPUT"

        _timeout_conversation, timeout_task = await _created(database, "timeout")
        celery.send_task("risk_platform.reliability.execute", args=[str(timeout_task), 1])
        timeout = await _wait(database, timeout_task, {DurableTaskStatus.RETRY_WAIT})
        assert timeout.failureCode == "AGENT_PROVIDER_UNAVAILABLE"
        assert timeout.nextRetryAt is not None

        cancelled_conversation, cancelled_task = await _created(database, "cancel")
        assert await request_cancellation(database, cancelled_conversation)
        celery.send_task("risk_platform.reliability.execute", args=[str(cancelled_task), 1])
        cancelled = await _wait(database, cancelled_task, {DurableTaskStatus.CANCELLED})
        assert cancelled.failureCode == "AGENT_EXECUTION_CANCELLED"

        async with database() as session:
            assert int(await session.scalar(select(func.count(Risk.id))) or 0) == before_risks
            assert int(await session.scalar(select(func.count(ActionItem.id))) or 0) == 0
            assert int(
                await session.scalar(select(func.count(AgentConfirmationToken.id))) or 0
            ) == 1
            preview_types = list(
                await session.scalars(
                    select(AgentEvent.type)
                    .where(AgentEvent.conversationId == preview_conversation)
                    .order_by(AgentEvent.sequence)
                )
            )
            assert preview_types == [
                AgentEventType.PROGRESS,
                AgentEventType.MESSAGE_DELTA,
                AgentEventType.PREVIEW,
                AgentEventType.COMPLETED,
            ]
            success_events = list(
                await session.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.conversationId == success_conversation)
                    .order_by(AgentEvent.sequence)
                )
            )
            assert [event.sequence for event in success_events] == list(
                range(1, len(success_events) + 1)
            )
            assert {event.payload["traceId"] for event in success_events} == {"t029-trace"}

    provider = FakeProvider()
    with real_worker(database, provider) as celery:
        asyncio.run(run(celery))
    assert any(request.get("toolResults") for request in provider.requests)


def test_real_worker_heartbeat_and_backpressure_are_persisted_and_fail_closed(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run(celery: Celery) -> None:
        heartbeat_conversation, heartbeat_task = await _created(database, "heartbeat")
        celery.send_task("risk_platform.reliability.execute", args=[str(heartbeat_task), 1])
        await _wait(database, heartbeat_task, {DurableTaskStatus.SUCCEEDED})
        async with database() as session:
            heartbeats = int(
                await session.scalar(
                    select(func.count(AgentEvent.id)).where(
                        AgentEvent.conversationId == heartbeat_conversation,
                        AgentEvent.type == AgentEventType.HEARTBEAT,
                    )
                )
                or 0
            )
        assert heartbeats >= 2

        backpressure_conversation, backpressure_task = await _created(database, "success")
        async with database.begin() as session:
            config = await session.scalar(
                select(AgentExecutionConfig).where(
                    AgentExecutionConfig.taskId == backpressure_task
                )
            )
            assert config is not None
            await append_event(
                session,
                conversation_id=backpressure_conversation,
                message_id=config.userMessageId,
                task_id=backpressure_task,
                event_type=AgentEventType.HEARTBEAT,
                payload={"padding": "x" * (1024 * 1024)},
            )
        celery.send_task("risk_platform.reliability.execute", args=[str(backpressure_task), 1])
        failed = await _wait(database, backpressure_task, {DurableTaskStatus.FAILED})
        assert failed.failureCode == "AGENT_STREAM_BACKPRESSURE"
        async with database() as session:
            terminal = await session.scalar(
                select(AgentEvent)
                .where(AgentEvent.conversationId == backpressure_conversation)
                .order_by(AgentEvent.sequence.desc())
                .limit(1)
            )
            assert terminal is not None
            assert terminal.type is AgentEventType.ERROR
            assert terminal.payload["code"] == "AGENT_STREAM_BACKPRESSURE"

    provider = FakeProvider()
    with real_worker(database, provider, heartbeat_interval=0.03) as celery:
        asyncio.run(run(celery))


def test_slow_tool_obeys_attempt_deadline_heartbeat_and_cancellation(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def timeout_run(celery: Celery) -> None:
        conversation_id, task_id = await _created(database, "slow-tool")
        celery.send_task("risk_platform.reliability.execute", args=[str(task_id), 1])
        task = await _wait(database, task_id, {DurableTaskStatus.RETRY_WAIT})
        assert task.failureCode == "AGENT_PROVIDER_UNAVAILABLE"
        async with database() as session:
            assert int(
                await session.scalar(
                    select(func.count(AgentEvent.id)).where(
                        AgentEvent.conversationId == conversation_id,
                        AgentEvent.type == AgentEventType.HEARTBEAT,
                    )
                )
                or 0
            ) >= 2

    provider = FakeProvider()
    slow = cast(AgentToolRegistry, SlowTools())
    with real_worker(
        database,
        provider,
        heartbeat_interval=0.02,
        attempt_timeout_seconds=0.08,
        registry=slow,
    ) as celery:
        asyncio.run(timeout_run(celery))

    async def cancellation_run(celery: Celery) -> None:
        conversation_id, task_id = await _created(database, "cancel-tool")
        celery.send_task("risk_platform.reliability.execute", args=[str(task_id), 1])
        await asyncio.sleep(0.05)
        assert await request_cancellation(database, conversation_id)
        task = await _wait(database, task_id, {DurableTaskStatus.CANCELLED})
        assert task.failureCode == "AGENT_EXECUTION_CANCELLED"
        assert not any(
            request.get("phase") == "RESPOND" and request.get("message") == "cancel-tool"
            for request in provider.requests
        )

    with real_worker(
        database,
        provider,
        heartbeat_interval=0.02,
        registry=slow,
    ) as celery:
        asyncio.run(cancellation_run(celery))

    async def cadence_run(celery: Celery) -> None:
        conversation_id, task_id = await _created(database, "cadence-tools")
        celery.send_task("risk_platform.reliability.execute", args=[str(task_id), 1])
        await _wait(database, task_id, {DurableTaskStatus.SUCCEEDED})
        async with database() as session:
            assert int(
                await session.scalar(
                    select(func.count(AgentEvent.id)).where(
                        AgentEvent.conversationId == conversation_id,
                        AgentEvent.type == AgentEventType.HEARTBEAT,
                    )
                )
                or 0
            ) >= 3

    cadence_tools = cast(AgentToolRegistry, SlowTools(delay=0.015))
    with real_worker(
        database,
        provider,
        heartbeat_interval=0.03,
        registry=cadence_tools,
    ) as celery:
        asyncio.run(cadence_run(celery))


def test_config_invalid_and_capacity_cancellation_have_terminal_events(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run(celery: Celery) -> None:
        conversation_id, task_id = await _created(database, "config-invalid")
        async with database.begin() as session:
            task = await session.get(DurableTask, task_id)
            assert task is not None
            task.payload = {**task.payload, "requested_by_user_id": str(uuid.uuid4())}
        celery.send_task("risk_platform.reliability.execute", args=[str(task_id), 1])
        failed = await _wait(database, task_id, {DurableTaskStatus.FAILED})
        assert failed.failureCode == "AGENT_EXECUTION_CONFIG_INVALID"
        async with database() as session:
            config_error = await session.scalar(
                select(AgentEvent)
                .where(AgentEvent.conversationId == conversation_id)
                .order_by(AgentEvent.sequence.desc())
                .limit(1)
            )
            assert config_error is not None
            assert config_error.payload["code"] == "AGENT_EXECUTION_CONFIG_INVALID"

        capacity_conversation, capacity_task = await _created(database, "capacity-cancel")
        async with database.begin() as session:
            config = await session.scalar(
                select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == capacity_task)
            )
            assert config is not None
            for _ in range(255):
                await append_event(
                    session,
                    conversation_id=capacity_conversation,
                    message_id=config.userMessageId,
                    task_id=capacity_task,
                    event_type=AgentEventType.HEARTBEAT,
                    payload={"traceId": "t029-trace"},
                )
        assert await request_cancellation(database, capacity_conversation)
        celery.send_task("risk_platform.reliability.execute", args=[str(capacity_task), 1])
        cancelled = await _wait(database, capacity_task, {DurableTaskStatus.CANCELLED})
        assert cancelled.failureCode == "AGENT_EXECUTION_CANCELLED"
        async with database() as session:
            events = list(
                await session.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.conversationId == capacity_conversation)
                    .order_by(AgentEvent.sequence)
                )
            )
            assert len(events) == 256
            assert events[-1].payload["code"] == "AGENT_EXECUTION_CANCELLED"

    with real_worker(database, FakeProvider()) as celery:
        asyncio.run(run(celery))


def test_postgresql_sse_resume_cursor_and_asgi_framing(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> None:
        conversation_id = await _latest_completed_conversation(database)
        async with database() as session:
            events = list(
                await session.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.conversationId == conversation_id)
                    .order_by(AgentEvent.sequence)
                )
            )
        assert len(events) >= 2
        stream = await open_event_stream(database, conversation_id, OWNER, events[0].id)
        chunks = [chunk async for chunk in stream]
        assert [chunk.split(b"\n", 1)[0] for chunk in chunks] == [
            f"id: {event.id}".encode() for event in events[1:]
        ]
        assert chunks[-1].find(b"event: completed") >= 0

        service = AgentConversationService(database)

        async def override_identity() -> SessionIdentity:
            return identity()

        def override_service() -> AgentConversationService:
            return service

        app = create_app(
            composition=AppComposition(
                routers=(router,),
                dependency_overrides={
                    current_identity: override_identity,
                    get_agent_service: override_service,
                },
            )
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            response = await client.get(
                f"/api/agent/conversations/{conversation_id}/events",
                params={"after": str(events[0].id)},
            )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert "event: completed" in response.text

        with pytest.raises(ApiError) as error:
            await open_event_stream(database, conversation_id, OWNER, uuid.uuid4())
        assert error.value.code == "AGENT_EVENT_CURSOR_UNRECOVERABLE"

        idle_conversation, idle_task = await _created(database, "idle")
        idle_stream = await open_event_stream(
            database,
            idle_conversation,
            OWNER,
            None,
            poll_interval=0.005,
            idle_seconds=0.02,
        )
        idle_chunks = [chunk async for chunk in idle_stream]
        assert len(idle_chunks) == 1
        assert b"AGENT_STREAM_IDLE_TIMEOUT" in idle_chunks[0]
        repeated_idle_stream = await open_event_stream(
            database,
            idle_conversation,
            OWNER,
            None,
            poll_interval=0.005,
            idle_seconds=0.02,
        )
        assert [chunk async for chunk in repeated_idle_stream] == []
        async with database() as session:
            idle_config = await session.scalar(
                select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == idle_task)
            )
            assert idle_config is not None
            assert idle_config.cancellationRequestedAt is None
            assert int(
                await session.scalar(
                    select(func.count(AgentEvent.id)).where(
                        AgentEvent.conversationId == idle_conversation,
                        AgentEvent.payload["code"].as_string() == "AGENT_STREAM_IDLE_TIMEOUT",
                    )
                )
                or 0
            ) == 1

        disconnect_conversation, disconnect_task = await _created(database, "disconnect")
        disconnect_stream = await open_event_stream(
            database,
            disconnect_conversation,
            OWNER,
            None,
            poll_interval=0.005,
            idle_seconds=60,
        )
        async def next_disconnect_chunk() -> bytes:
            return await anext(disconnect_stream)

        pending: asyncio.Task[bytes] = asyncio.create_task(next_disconnect_chunk())
        await asyncio.sleep(0.02)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await cast(AsyncGenerator[bytes, None], disconnect_stream).aclose()
        async with database() as session:
            disconnect_config = await session.scalar(
                select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == disconnect_task)
            )
            assert disconnect_config is not None
            assert disconnect_config.cancellationRequestedAt is not None

    asyncio.run(run())


async def _latest_completed_conversation(
    factory: async_sessionmaker[AsyncSession],
) -> UUID:
    async with factory() as session:
        value = await session.scalar(
            select(AgentEvent.conversationId)
            .where(AgentEvent.type == AgentEventType.COMPLETED)
            .order_by(AgentEvent.createdAt.desc())
            .limit(1)
        )
        assert value is not None
        return value
