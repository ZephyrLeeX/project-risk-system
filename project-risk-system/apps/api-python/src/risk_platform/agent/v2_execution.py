"""Durable bridge from the established Agent task/event system to V2 native tools."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.v2_adapter import ProviderError
from risk_platform.auth.repository import AuthRepository
from risk_platform.auth.service import AuthService, SessionIdentity
from risk_platform.model_types import JSONValue
from risk_platform.reliability.core import TaskHandler
from risk_platform.reliability.dispatcher import DurableTaskCancelled, DurableTaskFailure
from risk_platform.reliability.models import DurableTask, DurableTaskStatus

from .core import AgentLoopError, ReadOnlyAgentCore
from .events import append_event, event_capacity_available
from .models import (
    AgentConversation,
    AgentEventType,
    AgentExecutionConfig,
    AgentMessage,
    AgentMessageRole,
)
from .schemas import CandidateRisk


class NativeAgentExecutionWorker:
    """Use the pre-existing fenced durable task and PostgreSQL event facts only."""

    with_context = True

    def __init__(self, sessions: async_sessionmaker[AsyncSession], core: ReadOnlyAgentCore) -> None:
        self._sessions = sessions
        self._core = core

    async def __call__(
        self, payload: Mapping[str, JSONValue], *, task_id: UUID, lease_token: UUID
    ) -> None:
        try:
            config, message, identity = await self._load(payload, task_id, lease_token)
            outcome = await self._core.run(identity, message.content)
            await self._complete(
                config, message, task_id, lease_token, outcome.text, outcome.candidate_risks
            )
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
        except DurableTaskFailure:
            raise
        except DurableTaskCancelled:
            raise
        except Exception:
            await self._error(
                payload, task_id, lease_token, "AGENT_INTERNAL_ERROR", retryable=False
            )
            raise DurableTaskFailure(
                "AGENT_INTERNAL_ERROR", retryable=False, summary="native agent execution failed"
            ) from None

    async def _load(
        self, payload: Mapping[str, JSONValue], task_id: UUID, lease_token: UUID
    ) -> tuple[AgentExecutionConfig, AgentMessage, SessionIdentity]:
        try:
            config_id = UUID(str(payload["execution_configuration_id"]))
        except (KeyError, TypeError, ValueError):
            raise DurableTaskFailure(
                "AGENT_EXECUTION_CONFIG_INVALID",
                retryable=False,
                summary="invalid execution payload",
            ) from None
        async with self._sessions() as session:
            config = await session.get(AgentExecutionConfig, config_id)
            task = await session.get(DurableTask, task_id)
            if (
                config is None
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
            return config, message, identity

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
        try:
            config_id = UUID(str(payload["execution_configuration_id"]))
        except (KeyError, TypeError, ValueError):
            return
        async with self._sessions.begin() as session:
            config = await self._assert_fence(
                session, config_id, task_id, lease_token, optional=True
            )
            if config is None:
                return
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

    async def _append(
        self,
        session: AsyncSession,
        config: AgentExecutionConfig,
        message: AgentMessage,
        task_id: UUID,
        kind: AgentEventType,
        payload: dict[str, object],
        message_id: UUID,
    ) -> None:
        event_payload = {**payload, "traceId": message.traceId}
        if not await event_capacity_available(session, config.conversationId, event_payload):
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
