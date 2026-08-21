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
from risk_platform.shared.errors import ApiError

from .context import (
    ActiveProject,
    ConversationContextPolicy,
    ConversationContextService,
    refers_to_active_project,
)
from .core import AgentCoreOutcome, AgentLoopError, ProjectSelectionRequired, ReadOnlyAgentCore
from .events import append_event, event_capacity_available
from .models import (
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
    MutationDraftStatus,
)
from .mutations import MutationConfirmationRequired
from .schemas import CandidateRisk


def _project_selection_resume_context(selected: Mapping[str, JSONValue]) -> str:
    """Render server-authorized selection *facts* as untrusted grounding data.

    Only the dynamic selection facts (id/name/code/department/status) live
    here; they are delivered as a bounded, explicitly-untrusted
    ``SERVER_GROUNDING_DATA`` message, never promoted to SYSTEM instruction
    authority.  The trust that the project was user-selected and
    DataScope-revalidated is enforced by the code-level ``selected_project_id``
    parameter (project_search removal and ``AGENT_PROJECT_REQUERY_FORBIDDEN``),
    not by this text.  The static guidance to use ``selectedProjectId``
    directly and never re-issue ``project_search`` lives in the SYSTEM
    instruction.
    """

    return (
        f"selectedProjectId={selected.get('id')}; "
        f"selectedProjectName={selected.get('name')}; "
        f"externalCode={selected.get('externalCode')}; "
        f"departmentName={selected.get('departmentName')}; "
        f"status={selected.get('status')}。"
    )


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
        # The constructed service holds a static fallback policy + the
        # conservative byte estimator; each execution overrides both with the
        # capability-derived token thresholds and provider estimator frozen
        # from that execution's snapshot (see ``_invoke_core``), so the same
        # immutable snapshot sizes both the loop budget and the conversation
        # context.
        budget = core.context_budget
        self._conversation_context = ConversationContextService(
            sessions,
            core.summarize_conversation,
            ConversationContextPolicy.from_budget(budget),
        )
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
                config,
                message,
                task_id,
                lease_token,
                outcome.text,
                outcome.candidate_risks,
                outcome.active_project,
            )
        except ProjectSelectionRequired as required:
            await self._wait_for_project_selection(
                payload, task_id, lease_token, required.candidates
            )
            return
        except MutationConfirmationRequired:
            # The core created the OPEN WRITE_CONFIRMATION interaction + draft
            # and moved the execution to WAITING_FOR_USER before raising; if a
            # cancel arrived mid-proposal, abandon the proposal instead of
            # leaving an OPEN confirmation for a cancelled turn.
            await self._abort_write_confirmation_if_cancelled(task_id, config)
            return
        except AgentLoopError as error:
            await self._error(payload, task_id, lease_token, error.code, retryable=False)
            raise DurableTaskFailure(
                error.code, retryable=False, summary="native agent loop limit"
            ) from None
        except ProviderError as error:
            # Adapter-owned retry/failover has already consumed the immutable
            # candidate snapshot. Only the existing durable retry lifecycle may
            # decide whether this is the final attempt. The dispatcher writes
            # the terminal Agent state only after DurableTask is FAILED, so a
            # RETRY_WAIT attempt cannot leak a terminal SSE error.
            raise DurableTaskFailure(
                "AGENT_PROVIDER_UNAVAILABLE",
                retryable=error.retryable,
                summary="provider candidates unavailable",
            ) from None
        except ApiError as error:
            # A domain/tool failure (Agent tool argument validation, scoped
            # risk-query validation, WEEKLY_REPORT_STALE, proposal validation)
            # is NOT a provider outage: it must never surface as
            # AGENT_PROVIDER_UNAVAILABLE, and it deserves a more precise
            # terminal code than the generic AGENT_INTERNAL_ERROR below.
            # Agent-scoped codes (AGENT_*) pass through unchanged; everything
            # else is classified as AGENT_TOOL_ERROR. Only the safe,
            # server-authored ApiError message/code cross the SSE boundary —
            # never exception text, SQL detail or infrastructure secrets.
            code = error.code if error.code.startswith("AGENT_") else "AGENT_TOOL_ERROR"
            await self._error(
                payload,
                task_id,
                lease_token,
                code,
                retryable=False,
                error_message=error.message,
                detail_code=error.code,
            )
            raise DurableTaskFailure(
                code, retryable=False, summary="agent tool or domain failure"
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
        selected_project_id = None
        if isinstance(selected, dict):
            context = _project_selection_resume_context(selected)
            try:
                selected_project_id = UUID(str(selected["id"]))
            except (KeyError, TypeError, ValueError):
                raise AgentLoopError("AGENT_EXECUTION_CONFIG_INVALID") from None
        call = asyncio.create_task(
            self._invoke_core(
                identity,
                message.content,
                context,
                config,
                execution,
                selected_project_id=selected_project_id,
            )
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
        *,
        selected_project_id: UUID | None,
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
        conversation_context = None
        snapshot = None
        if "conversation_context" in parameters:
            snapshot = await self._core.candidate_snapshot()
            # Freeze the per-execution token budget and the provider-specific
            # estimator from the same immutable snapshot captured here, so a
            # provider-admin change made mid-execution only affects the next
            # execution's snapshot — never this one's loop budget or memory
            # sizing.  The conversation context is built with the
            # capability-derived policy + estimator so memory compression
            # reflects the real model context window (tokens), not serialized
            # bytes, and the same snapshot is handed to ``core.run`` so the
            # loop's fail-closed budget uses the identical frozen capability.
            budget = self._core.execution_budget(snapshot)
            estimator = self._core.estimator_for(snapshot)
            policy = ConversationContextPolicy.from_budget(budget)
            history_budget = self._core.history_budget_for(
                identity,
                message,
                resume_context=context,
                selected_project_id=selected_project_id,
                snapshot=snapshot,
            )
            conversation_context = await self._conversation_context.build(
                config.conversationId,
                config.userMessageId,
                identity,
                snapshot,
                history_budget=history_budget,
                policy=policy,
                estimator=estimator,
            )
            if (
                selected_project_id is None
                and conversation_context.active_project is not None
                and refers_to_active_project(message)
            ):
                selected_project_id = conversation_context.active_project.id
        if "conversation_context" in parameters:
            return await self._core.run(
                identity,
                message,
                context,
                conversation_id=config.conversationId,
                execution_id=None if execution is None else execution.id,
                selected_project_id=selected_project_id,
                conversation_context=conversation_context,
                candidate_snapshot=snapshot,
            )
        if context is None:
            if "selected_project_id" in parameters:
                return await self._core.run(
                    identity,
                    message,
                    conversation_id=config.conversationId,
                    execution_id=None if execution is None else execution.id,
                    selected_project_id=selected_project_id,
                )
            return await self._core.run(
                identity,
                message,
                conversation_id=config.conversationId,
                execution_id=None if execution is None else execution.id,
            )
        if "selected_project_id" in parameters:
            return await self._core.run(
                identity,
                message,
                context,
                conversation_id=config.conversationId,
                execution_id=None if execution is None else execution.id,
                selected_project_id=selected_project_id,
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
        cancelled = False
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
            # Post-core cancellation fence: a cancel that landed between the
            # core raising ProjectSelectionRequired (or a resumed
            # selectionCandidates turn) and this state transition must not
            # create an OPEN PROJECT_SELECTION interaction for a cancelled
            # turn. Re-read the flag fresh in this transaction; on cancel,
            # terminalize the execution CANCELLED here and raise OUTSIDE the
            # ``async with`` so the terminalization commits. Raising inside
            # would roll it back and leave the execution RUNNING: when this
            # method is reached via the ``except ProjectSelectionRequired``
            # handler, a raise from within that handler is NOT caught by the
            # sibling ``except DurableTaskCancelled`` in ``__call__`` (which
            # would otherwise terminalize), so this method must terminalize
            # itself — mirroring ``_abort_write_confirmation_if_cancelled``.
            # The interaction is never created (the raise precedes it), so no
            # OPEN PROJECT_SELECTION is left for a cancelled turn.
            fence_config = await session.scalar(
                select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == task_id)
            )
            if fence_config is None or fence_config.cancellationRequestedAt is not None:
                now = datetime.now(UTC)
                if execution.status not in {
                    AgentExecutionStatus.COMPLETED,
                    AgentExecutionStatus.CANCELLED,
                }:
                    execution.status = AgentExecutionStatus.CANCELLED
                    execution.completedAt = now
                cancelled = True
            else:
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
        # The transaction above committed either the CANCELLED terminalization
        # (cancel fence) or the OPEN interaction + WAITING_FOR_USER (happy
        # path). Raising outside the ``async with`` keeps the terminalization;
        # the dispatcher's ``DurableTaskCancelled`` path then marks the task
        # CANCELLED.
        if cancelled:
            raise DurableTaskCancelled

    async def _complete(
        self,
        config: AgentExecutionConfig,
        user_message: AgentMessage,
        task_id: UUID,
        lease_token: UUID,
        text: str,
        candidate_risks: tuple[CandidateRisk, ...] = (),
        active_project: ActiveProject | None = None,
    ) -> None:
        async with self._sessions.begin() as session:
            fence_config = await self._assert_fence(session, config.id, task_id, lease_token)
            assert fence_config is not None  # _assert_fence raises when optional=False
            # Post-core cancellation fence: a cancel that landed between the
            # core returning and this completion must not write a normal
            # COMPLETED assistant result for a turn the user asked to stop.
            # _assert_fence re-read the config fresh in this transaction, so
            # cancellationRequestedAt is current; raising here rolls the
            # transaction back so no COMPLETED event or assistant message is
            # appended, and the DurableTaskCancelled handler terminalizes the
            # execution CANCELLED.
            if fence_config.cancellationRequestedAt is not None:
                raise DurableTaskCancelled
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
            if active_project is not None:
                conversation.activeProjectId = active_project.id
                conversation.activeProjectName = active_project.name
                conversation.contextUpdatedAt = datetime.now(UTC)
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
        error_message: str | None = None,
        detail_code: str | None = None,
    ) -> None:
        # ``error_message``/``detail_code`` are only ever the safe,
        # server-authored ApiError message/code (client-safe by contract — the
        # same text the REST envelope returns); they never carry exception
        # text, SQL detail or provider secrets.
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
                error_payload: dict[str, object] = {"code": code, "retryable": retryable}
                if error_message is not None:
                    error_payload["message"] = error_message
                if detail_code is not None:
                    error_payload["detailCode"] = detail_code
                await self._append(
                    session,
                    config,
                    message,
                    task_id,
                    AgentEventType.ERROR,
                    error_payload,
                    message.id,
                )

    async def finalize_task_failure(
        self, session: AsyncSession, task_id: UUID, failure_code: str
    ) -> None:
        """Persist the provider terminal state after durable exhaustion.

        This is called by the dispatcher in the same transaction that changes
        the DurableTask to FAILED. It is deliberately idempotent: a duplicate
        delivery or recovery pass cannot create a second terminal event or
        move an already-terminal execution backwards.
        """

        if failure_code != "AGENT_PROVIDER_UNAVAILABLE":
            return
        execution = await session.scalar(
            select(AgentExecution).where(AgentExecution.taskId == task_id).with_for_update()
        )
        if execution is None:
            return
        config = await session.scalar(
            select(AgentExecutionConfig).where(AgentExecutionConfig.taskId == task_id)
        )
        if config is None:
            return
        existing_terminal = await session.scalar(
            select(AgentEvent.id)
            .where(
                AgentEvent.taskId == task_id,
                AgentEvent.type.in_({AgentEventType.COMPLETED, AgentEventType.ERROR}),
            )
            .limit(1)
        )
        if existing_terminal is not None:
            if execution.status not in {
                AgentExecutionStatus.COMPLETED,
                AgentExecutionStatus.CANCELLED,
            }:
                execution.status = AgentExecutionStatus.FAILED
                execution.completedAt = datetime.now(UTC)
            return
        if execution.status not in {
            AgentExecutionStatus.COMPLETED,
            AgentExecutionStatus.CANCELLED,
        }:
            execution.status = AgentExecutionStatus.FAILED
            execution.completedAt = datetime.now(UTC)
        message = await session.get(AgentMessage, config.userMessageId)
        if message is None:
            return
        await self._append(
            session,
            config,
            message,
            task_id,
            AgentEventType.ERROR,
            {
                "code": "AGENT_PROVIDER_UNAVAILABLE",
                "message": "AI 服务暂时无法连接，请稍后重试",
            },
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

    async def _abort_write_confirmation_if_cancelled(
        self,
        task_id: UUID,
        config: AgentExecutionConfig,
    ) -> None:
        """Abandon a write proposal whose core finished during an explicit cancel.

        The core creates the OPEN WRITE_CONFIRMATION interaction + draft and
        moves the execution to WAITING_FOR_USER inside its own transaction
        BEFORE raising ``MutationConfirmationRequired``, so by the time the
        worker catches it the paused state already exists. Re-read the cancel
        flag; if a cancel arrived while the core was proposing, close the
        interaction + draft (CANCELLED), terminalize the execution CANCELLED,
        and raise ``DurableTaskCancelled`` so the dispatcher marks the task
        CANCELLED — leaving no OPEN confirmation for a turn the user asked to
        stop. If no cancel is pending, return and leave the OPEN confirmation
        for the user to resolve.
        """

        async with self._sessions.begin() as session:
            fresh = await session.get(AgentExecutionConfig, config.id)
            if fresh is None or fresh.cancellationRequestedAt is None:
                return
            execution = await session.scalar(
                select(AgentExecution).where(AgentExecution.taskId == task_id).with_for_update()
            )
            if execution is None:
                return
            now = datetime.now(UTC)
            if execution.status not in {
                AgentExecutionStatus.COMPLETED,
                AgentExecutionStatus.CANCELLED,
            }:
                execution.status = AgentExecutionStatus.CANCELLED
                execution.completedAt = now
            interaction = await session.scalar(
                select(AgentInteraction)
                .where(
                    AgentInteraction.executionId == execution.id,
                    AgentInteraction.status == AgentInteractionStatus.OPEN,
                )
                .with_for_update()
            )
            if interaction is not None:
                interaction.status = AgentInteractionStatus.CANCELLED
                interaction.responseAction = AgentInteractionAction.CANCEL
                interaction.resolvedAt = now
                draft = await session.scalar(
                    select(MutationDraft).where(MutationDraft.interactionId == interaction.id)
                )
                if draft is not None:
                    draft.status = MutationDraftStatus.CANCELLED
                    draft.resolvedAt = now
        # The cleanup transaction committed above; raise OUTSIDE the ``async
        # with`` so the dispatcher's DurableTaskCancelled path marks the task
        # CANCELLED (raising inside would roll the cleanup back).
        raise DurableTaskCancelled

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
