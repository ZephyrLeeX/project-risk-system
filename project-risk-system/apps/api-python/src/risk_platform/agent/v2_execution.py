"""Durable bridge from the established Agent task/event system to V2 native tools."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.v2_adapter import ProviderError, ProviderErrorClassification
from risk_platform.auth.repository import AuthRepository
from risk_platform.auth.service import AuthService, SessionIdentity
from risk_platform.model_types import JSONValue
from risk_platform.reliability.core import TaskHandler, heartbeat
from risk_platform.reliability.dispatcher import DurableTaskCancelled, DurableTaskFailure
from risk_platform.reliability.models import DurableTask, DurableTaskStatus

from .core import AgentCoreOutcome, AgentLoopError, ProjectSelectionRequired, ReadOnlyAgentCore
from .events import append_event, event_capacity_available
from .models import (
    AgentConversation,
    AgentEventType,
    AgentExecution,
    AgentExecutionConfig,
    AgentExecutionStatus,
    AgentInteraction,
    AgentInteractionStatus,
    AgentInteractionType,
    AgentMessage,
    AgentMessageRole,
)
from .mutations import MutationConfirmationRequired
from .schemas import CandidateRisk


class NativeAgentExecutionWorker:
    """Use the pre-existing fenced durable task and PostgreSQL event facts only."""

    with_context = True

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        core: ReadOnlyAgentCore,
        *,
        heartbeat_interval: float = 15.0,
        attempt_timeout_seconds: float | None = None,
    ) -> None:
        self._sessions = sessions
        self._core = core
        self._heartbeat_interval = heartbeat_interval
        self._attempt_timeout_seconds = attempt_timeout_seconds

    async def __call__(
        self, payload: Mapping[str, JSONValue], *, task_id: UUID, lease_token: UUID
    ) -> None:
        try:
            config, execution, message, identity = await self._load(payload, task_id, lease_token)
            if execution is not None and execution.status is not AgentExecutionStatus.RUNNING:
                raise DurableTaskFailure(
                    "AGENT_EXECUTION_CONFIG_INVALID",
                    retryable=False,
                    summary="execution is not running",
                )
            pending_candidates = (
                None if execution is None else execution.resumeContext.get("selectionCandidates")
            )
            if isinstance(pending_candidates, list) and all(
                isinstance(item, dict) for item in pending_candidates
            ):
                await self._wait_for_project_selection(
                    payload,
                    task_id,
                    lease_token,
                    tuple(cast(dict[str, JSONValue], item) for item in pending_candidates),
                )
                return
            outcome = await self._run_with_heartbeat(
                config, execution, message, identity, task_id, lease_token
            )
            await self._complete(
                config, message, task_id, lease_token, outcome.text, outcome.candidate_risks
            )
        except ProjectSelectionRequired as required:
            await self._wait_for_project_selection(
                payload, task_id, lease_token, required.candidates
            )
            return
        except MutationConfirmationRequired:
            return
        except AgentLoopError as error:
            await self._error(payload, task_id, lease_token, error.code, retryable=False)
            raise DurableTaskFailure(
                error.code, retryable=False, summary="native agent loop limit"
            ) from None
        except ProviderError as error:
            # Adapter-owned retry/failover has already consumed the immutable
            # candidate snapshot. Only the existing durable retry lifecycle may
            # decide a later execution attempt; no transient SSE terminal fact.
            if not error.retryable:
                await self._error(
                    payload,
                    task_id,
                    lease_token,
                    "AGENT_PROVIDER_UNAVAILABLE",
                    retryable=False,
                )
            raise DurableTaskFailure(
                "AGENT_PROVIDER_UNAVAILABLE",
                retryable=error.retryable,
                summary="provider candidates unavailable",
            ) from None
        except DurableTaskFailure as error:
            if error.code == "AGENT_STREAM_BACKPRESSURE":
                await self._error(
                    payload,
                    task_id,
                    lease_token,
                    error.code,
                    retryable=False,
                )
            raise
        except DurableTaskCancelled:
            await self._mark_execution_terminal(payload, task_id, AgentExecutionStatus.CANCELLED)
            raise
        except Exception:
            await self._error(
                payload, task_id, lease_token, "AGENT_INTERNAL_ERROR", retryable=False
            )
            raise DurableTaskFailure(
                "AGENT_INTERNAL_ERROR", retryable=False, summary="native agent execution failed"
            ) from None

    async def _run_with_heartbeat(
        self,
        config: AgentExecutionConfig,
        execution: AgentExecution | None,
        message: AgentMessage,
        identity: SessionIdentity,
        task_id: UUID,
        lease_token: UUID,
    ) -> AgentCoreOutcome:
        """Run the native loop with durable liveness and cancellation boundaries."""

        selected = None if execution is None else execution.resumeContext.get("selectedProject")
        context = None
        if isinstance(selected, dict):
            context = (
                f"用户已选择项目: {selected.get('name')} "
                f"(项目状态: {selected.get('status')})。请继续回答原问题。"
            )
        call = asyncio.create_task(
            self._invoke_core(identity, message.content, context, config, execution)
        )
        started = asyncio.get_running_loop().time()
        try:
            while not call.done():
                timeout = self._heartbeat_interval
                if self._attempt_timeout_seconds is not None:
                    timeout = min(
                        timeout,
                        max(
                            0.0,
                            self._attempt_timeout_seconds
                            - (asyncio.get_running_loop().time() - started),
                        ),
                    )
                done, _ = await asyncio.wait({call}, timeout=timeout)
                if done:
                    break
                async with self._sessions.begin() as session:
                    current = await session.get(AgentExecutionConfig, config.id)
                    if current is None or current.cancellationRequestedAt is not None:
                        call.cancel()
                        raise DurableTaskCancelled
                    if not await heartbeat(session, task_id, lease_token):
                        call.cancel()
                        raise DurableTaskFailure(
                            "AGENT_EXECUTION_CONFIG_INVALID",
                            retryable=False,
                            summary="stale agent lease",
                        )
                    await self._append(
                        session,
                        config,
                        message,
                        task_id,
                        AgentEventType.HEARTBEAT,
                        {"heartbeat": True},
                        message.id,
                    )
                if (
                    self._attempt_timeout_seconds is not None
                    and asyncio.get_running_loop().time() - started >= self._attempt_timeout_seconds
                ):
                    call.cancel()
                    raise ProviderError(
                        ProviderErrorClassification.NETWORK,
                        retryable=True,
                        failover_allowed=False,
                    )
            return await call
        finally:
            if not call.done():
                call.cancel()
            with suppress(asyncio.CancelledError):
                await call

    async def _invoke_core(
        self,
        identity: SessionIdentity,
        message: str,
        context: str | None,
        config: AgentExecutionConfig,
        execution: AgentExecution | None,
    ) -> AgentCoreOutcome:
        """Invoke the V2 core while keeping old test doubles source-compatible.

        Production ``ReadOnlyAgentCore`` accepts the durable execution context.
        A few integration fixtures intentionally use a minimal ``run(identity,
        message)`` double; detecting that shape avoids turning a fixture
        composition mismatch into a false ``AGENT_INTERNAL_ERROR`` without
        weakening the production contract or catching real runtime TypeErrors.
        """
        parameters = inspect.signature(self._core.run).parameters
        if "conversation_id" not in parameters:
            return await self._core.run(identity, message)
        if context is None:
            return await self._core.run(
                identity,
                message,
                conversation_id=config.conversationId,
                execution_id=None if execution is None else execution.id,
            )
        return await self._core.run(
            identity,
            message,
            context,
            conversation_id=config.conversationId,
            execution_id=None if execution is None else execution.id,
        )

    async def _load(
        self, payload: Mapping[str, JSONValue], task_id: UUID, lease_token: UUID
    ) -> tuple[AgentExecutionConfig, AgentExecution | None, AgentMessage, SessionIdentity]:
        try:
            config_id_value = payload.get("execution_configuration_id")
            execution_id_value = payload.get("execution_id")
            config_id = UUID(str(config_id_value)) if config_id_value else None
            execution_id = UUID(str(execution_id_value)) if execution_id_value else None
        except (KeyError, TypeError, ValueError):
            raise DurableTaskFailure(
                "AGENT_EXECUTION_CONFIG_INVALID",
                retryable=False,
                summary="invalid execution payload",
            ) from None
        async with self._sessions() as session:
            config = (
                await session.get(AgentExecutionConfig, config_id)
                if config_id is not None
                else await session.scalar(
                    select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == task_id)
                )
            )
            execution = await session.scalar(
                select(AgentExecution).where(AgentExecution.taskId == task_id)
            )
            if execution is None and execution_id is not None:
                execution = await session.get(AgentExecution, execution_id)
            task = await session.get(DurableTask, task_id)
            if (
                config is None
                or execution is None
                or task is None
                or config.taskId != task_id
                or task.status is not DurableTaskStatus.RUNNING
                or task.leaseToken != lease_token
                or config.cancellationRequestedAt is not None
            ):
                if config is not None and config.cancellationRequestedAt is not None:
                    raise DurableTaskCancelled
                raise DurableTaskFailure(
                    "AGENT_EXECUTION_CONFIG_INVALID",
                    retryable=False,
                    summary="stale agent execution",
                )
            message = await session.get(AgentMessage, config.userMessageId)
            repository = AuthRepository(session)
            user = await repository.user_by_id(config.requestedByUserId, for_update=False)
            if message is None or user is None:
                raise DurableTaskFailure(
                    "AGENT_EXECUTION_CONFIG_INVALID",
                    retryable=False,
                    summary="missing agent context",
                )
            identity = SessionIdentity(
                session_id=UUID(int=0),
                expires_at=datetime.max.replace(tzinfo=UTC),
                user=AuthService._authenticated_user(user, await repository.user_access(user.id)),
            )
            return config, execution, message, identity

    async def _wait_for_project_selection(
        self,
        payload: Mapping[str, JSONValue],
        task_id: UUID,
        lease_token: UUID,
        candidates: tuple[dict[str, JSONValue], ...],
    ) -> None:
        execution_id = payload.get("execution_id")
        if execution_id is None:
            async with self._sessions() as read_session:
                config = await read_session.scalar(
                    select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == task_id)
                )
                execution_row = await read_session.scalar(
                    select(AgentExecution).where(AgentExecution.taskId == task_id)
                )
                execution_id = (
                    None if config is None or execution_row is None else str(execution_row.id)
                )
        if execution_id is None:
            raise DurableTaskFailure(
                "AGENT_EXECUTION_CONFIG_INVALID", retryable=False, summary="missing execution"
            )
        async with self._sessions.begin() as session:
            execution = await session.scalar(
                select(AgentExecution)
                .where(AgentExecution.id == UUID(str(execution_id)))
                .with_for_update()
            )
            if execution is None or execution.taskId != task_id:
                raise DurableTaskFailure(
                    "AGENT_EXECUTION_CONFIG_INVALID", retryable=False, summary="stale execution"
                )
            message = await session.get(AgentMessage, execution.userMessageId)
            if message is None:
                raise DurableTaskFailure(
                    "AGENT_EXECUTION_CONFIG_INVALID", retryable=False, summary="missing message"
                )
            interaction = AgentInteraction(
                executionId=execution.id,
                conversationId=execution.conversationId,
                ownerUserId=execution.requestedByUserId,
                type=AgentInteractionType.PROJECT_SELECTION,
                status=AgentInteractionStatus.OPEN,
                candidateOptions=[cast(JSONValue, item) for item in candidates],
                resumeContext=execution.resumeContext,
                expiresAt=datetime.now(UTC) + timedelta(minutes=30),
            )
            session.add(interaction)
            await session.flush()
            execution.status = AgentExecutionStatus.WAITING_FOR_USER
            execution.updatedAt = datetime.now(UTC)
            await self._append(
                session,
                execution,
                message,
                task_id,
                AgentEventType.INTERACTION_REQUIRED,
                {
                    "interactionId": str(interaction.id),
                    "type": interaction.type.value,
                    "candidates": [cast(object, item) for item in candidates],
                },
                message.id,
            )

    async def _complete(
        self,
        config: AgentExecutionConfig,
        user_message: AgentMessage,
        task_id: UUID,
        lease_token: UUID,
        text: str,
        candidate_risks: tuple[CandidateRisk, ...] = (),
    ) -> None:
        async with self._sessions.begin() as session:
            await self._assert_fence(session, config.id, task_id, lease_token)
            conversation = await session.scalar(
                select(AgentConversation)
                .where(AgentConversation.id == config.conversationId)
                .with_for_update()
            )
            if conversation is None:
                raise DurableTaskFailure(
                    "AGENT_EXECUTION_CONFIG_INVALID",
                    retryable=False,
                    summary="missing conversation",
                )
            execution = await session.scalar(
                select(AgentExecution).where(AgentExecution.taskId == task_id).with_for_update()
            )
            if execution is not None:
                execution.status = AgentExecutionStatus.COMPLETED
                execution.completedAt = datetime.now(UTC)
            assistant = AgentMessage(
                conversationId=conversation.id,
                sequence=conversation.lastMessageSequence + 1,
                role=AgentMessageRole.ASSISTANT,
                content=text,
                structured=(
                    {"candidateRisks": [risk.model_dump(mode="json") for risk in candidate_risks]}
                    if candidate_risks
                    else None
                ),
                traceId=user_message.traceId,
                dataAsOf=datetime.now(UTC),
            )
            session.add(assistant)
            await session.flush()
            await self._append(
                session,
                config,
                user_message,
                task_id,
                AgentEventType.MESSAGE_DELTA,
                {"text": text},
                assistant.id,
            )
            await self._append(
                session,
                config,
                user_message,
                task_id,
                AgentEventType.COMPLETED,
                {"dataAsOf": datetime.now(UTC).isoformat()},
                assistant.id,
            )

    async def _error(
        self,
        payload: Mapping[str, JSONValue],
        task_id: UUID,
        lease_token: UUID,
        code: str,
        *,
        retryable: bool,
    ) -> None:
        async with self._sessions.begin() as session:
            config_id_value = payload.get("execution_configuration_id")
            config_id = UUID(str(config_id_value)) if config_id_value is not None else None
            if config_id is None:
                config_row = await session.scalar(
                    select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == task_id)
                )
                config_id = None if config_row is None else config_row.id
            if config_id is None:
                return
            config = await self._assert_fence(
                session, config_id, task_id, lease_token, optional=True
            )
            if config is None:
                return
            execution = await session.scalar(
                select(AgentExecution).where(AgentExecution.taskId == task_id).with_for_update()
            )
            if execution is not None:
                execution.status = AgentExecutionStatus.FAILED
                execution.completedAt = datetime.now(UTC)
            message = await session.get(AgentMessage, config.userMessageId)
            if message is not None:
                await self._append(
                    session,
                    config,
                    message,
                    task_id,
                    AgentEventType.ERROR,
                    {"code": code, "retryable": retryable},
                    message.id,
                )

    async def _mark_execution_terminal(
        self,
        payload: Mapping[str, JSONValue],
        task_id: UUID,
        status: AgentExecutionStatus,
    ) -> None:
        execution_id = payload.get("execution_id")
        async with self._sessions.begin() as session:
            execution = (
                await session.get(AgentExecution, UUID(str(execution_id)))
                if execution_id is not None
                else await session.scalar(
                    select(AgentExecution).where(AgentExecution.taskId == task_id)
                )
            )
            if execution is not None:
                execution.status = status
                execution.completedAt = datetime.now(UTC)

    async def _append(
        self,
        session: AsyncSession,
        config: AgentExecutionConfig | AgentExecution,
        message: AgentMessage,
        task_id: UUID,
        kind: AgentEventType,
        payload: dict[str, object],
        message_id: UUID,
    ) -> None:
        event_payload = {**payload, "traceId": message.traceId}
        if kind is not AgentEventType.ERROR and not await event_capacity_available(
            session, config.conversationId, event_payload
        ):
            raise DurableTaskFailure(
                "AGENT_STREAM_BACKPRESSURE", retryable=False, summary="agent event capacity reached"
            )
        await append_event(
            session,
            conversation_id=config.conversationId,
            message_id=message_id,
            task_id=task_id,
            event_type=kind,
            payload=event_payload,
        )

    @staticmethod
    async def _assert_fence(
        session: AsyncSession,
        config_id: UUID,
        task_id: UUID,
        lease_token: UUID,
        *,
        optional: bool = False,
    ) -> AgentExecutionConfig | None:
        task = await session.scalar(
            select(DurableTask).where(DurableTask.id == task_id).with_for_update()
        )
        config = await session.get(AgentExecutionConfig, config_id)
        if (
            config is None
            or task is None
            or config.taskId != task_id
            or task.leaseToken != lease_token
        ):
            if optional:
                return None
            raise DurableTaskFailure(
                "AGENT_EXECUTION_CONFIG_INVALID", retryable=False, summary="stale agent execution"
            )
        return config


def native_agent_execution_handlers(
    sessions: async_sessionmaker[AsyncSession], core: ReadOnlyAgentCore
) -> Mapping[str, TaskHandler]:
    return {"AGENT_EXECUTION": cast(TaskHandler, NativeAgentExecutionWorker(sessions, core))}
