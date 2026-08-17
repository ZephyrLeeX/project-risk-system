"""PostgreSQL-only Agent SSE ordering, resume, and cancellation boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import cast as type_cast
from uuid import UUID

from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.reliability.models import DurableTask, DurableTaskStatus
from risk_platform.shared.errors import ApiError

from .models import (
    AgentConversation,
    AgentEvent,
    AgentEventType,
    AgentExecutionConfig,
)

MAX_PENDING_EVENTS = 256
MAX_PENDING_BYTES = 1024 * 1024
TERMINAL_EVENT_RESERVE_BYTES = 4096
ACTIVE_STATUSES = (
    DurableTaskStatus.QUEUED,
    DurableTaskStatus.RUNNING,
    DurableTaskStatus.RETRY_WAIT,
)
TERMINAL_EVENT_TYPES = {AgentEventType.COMPLETED, AgentEventType.ERROR}
STREAM_CLOSE_EVENT_TYPES = {
    AgentEventType.COMPLETED,
    AgentEventType.ERROR,
    AgentEventType.INTERACTION_REQUIRED,
}


async def append_event(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    message_id: UUID,
    task_id: UUID,
    event_type: AgentEventType,
    payload: Mapping[str, object],
) -> AgentEvent:
    """Append one event while holding the conversation sequence lock."""

    conversation = await session.scalar(
        select(AgentConversation).where(AgentConversation.id == conversation_id).with_for_update()
    )
    if conversation is None:
        raise RuntimeError("AGENT_EXECUTION_CONFIG_INVALID")
    terminal_event = await session.scalar(
        select(AgentEvent.id)
        .where(
            AgentEvent.taskId == task_id,
            AgentEvent.type.in_(TERMINAL_EVENT_TYPES),
        )
        .limit(1)
    )
    if terminal_event is not None:
        raise RuntimeError("AGENT_EVENT_AFTER_TERMINAL")
    event = AgentEvent(
        conversationId=conversation_id,
        messageId=message_id,
        taskId=task_id,
        sequence=conversation.lastEventSequence + 1,
        type=event_type,
        payload=dict(payload),
    )
    session.add(event)
    await session.flush()
    # PostgreSQL's contiguous-sequence trigger advances the conversation row;
    # refresh the identity-map instance before the next event in this unit of
    # work is appended.
    await session.refresh(conversation)
    return event


async def event_capacity_available(
    session: AsyncSession, conversation_id: UUID, payload: Mapping[str, object]
) -> bool:
    """Enforce the approved retained-event count and payload byte ceilings."""

    count, payload_bytes = (
        await session.execute(
            select(
                func.count(AgentEvent.id),
                func.coalesce(func.sum(func.octet_length(cast(AgentEvent.payload, Text))), 0),
            ).where(AgentEvent.conversationId == conversation_id)
        )
    ).one()
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Reserve one final event slot for the required backpressure error.
    return (
        int(count) < MAX_PENDING_EVENTS - 1
        and int(payload_bytes) + len(encoded) <= MAX_PENDING_BYTES - TERMINAL_EVENT_RESERVE_BYTES
    )


def wire_event(event: AgentEvent) -> bytes:
    data = {
        "conversationId": str(event.conversationId),
        "messageId": str(event.messageId),
        "sequence": event.sequence,
        "traceId": event.payload.get("traceId", ""),
        "occurredAt": event.createdAt.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        **event.payload,
    }
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type.value}\ndata: {encoded}\n\n".encode()


async def open_event_stream(
    sessions: async_sessionmaker[AsyncSession],
    conversation_id: UUID,
    owner_id: UUID,
    after: UUID | None,
    *,
    poll_interval: float = 1.0,
    idle_seconds: float = 60.0,
    keepalive_seconds: float = 15.0,
) -> AsyncIterator[bytes]:
    """Validate before headers are sent, then return a PostgreSQL-backed stream."""

    async with sessions() as session:
        conversation = await session.scalar(
            select(AgentConversation).where(
                AgentConversation.id == conversation_id, AgentConversation.ownerUserId == owner_id
            )
        )
        if conversation is None:
            raise ApiError(404, "AGENT_CONVERSATION_NOT_FOUND", "Agent 会话不存在或不属于当前用户")
        after_sequence = conversation.lastEventSequence
        if after is not None:
            cursor = await session.scalar(
                select(AgentEvent).where(
                    AgentEvent.id == after, AgentEvent.conversationId == conversation_id
                )
            )
            if cursor is None:
                raise ApiError(
                    409,
                    "AGENT_EVENT_CURSOR_UNRECOVERABLE",
                    "事件游标无法恢复",
                    data={"restartFrom": "conversation"},
                )
            after_sequence = cursor.sequence
    return _stream(
        sessions,
        conversation_id,
        after_sequence,
        poll_interval=poll_interval,
        idle_seconds=idle_seconds,
        keepalive_seconds=keepalive_seconds,
    )


async def _stream(
    sessions: async_sessionmaker[AsyncSession],
    conversation_id: UUID,
    after_sequence: int,
    *,
    poll_interval: float,
    idle_seconds: float,
    keepalive_seconds: float,
) -> AsyncIterator[bytes]:
    terminal_seen = False
    normal_close = False
    loop = asyncio.get_running_loop()
    last_fact_at = loop.time()
    last_keepalive_at = last_fact_at
    try:
        while True:
            async with sessions() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(AgentEvent)
                            .where(
                                AgentEvent.conversationId == conversation_id,
                                AgentEvent.sequence > after_sequence,
                            )
                            .order_by(AgentEvent.sequence)
                            .limit(MAX_PENDING_EVENTS)
                        )
                    ).all()
                )
            if rows:
                for row in rows:
                    after_sequence = row.sequence
                    terminal_seen = row.type in STREAM_CLOSE_EVENT_TYPES
                    yield wire_event(row)
                last_fact_at = loop.time()
                if terminal_seen:
                    normal_close = True
                    return
                continue
            status = await _latest_task_status(sessions, conversation_id)
            now = loop.time()
            if status in (DurableTaskStatus.QUEUED, DurableTaskStatus.RETRY_WAIT):
                # Queueing and durable retry backoff are expected periods without
                # AgentEvent facts. Keep the HTTP transport alive without changing
                # the execution's durable outcome or resume cursor.
                if now - last_keepalive_at >= keepalive_seconds:
                    yield b": keepalive\n\n"
                    last_keepalive_at = now
            elif status == DurableTaskStatus.RUNNING and now - last_fact_at >= idle_seconds:
                # A RUNNING execution should write durable heartbeats. This is a
                # delivery watchdog only: closing lets EventSource reconnect while
                # leaving task lifecycle ownership with the fenced worker.
                normal_close = True
                return
            elif status not in ACTIVE_STATUSES:
                normal_close = True
                return
            await asyncio.sleep(poll_interval)
    finally:
        if not normal_close and not terminal_seen:
            await request_cancellation(sessions, conversation_id)


async def request_cancellation(
    sessions: async_sessionmaker[AsyncSession], conversation_id: UUID
) -> bool:
    """Atomically mark only the active execution; never cancel its task directly."""

    async with sessions.begin() as session:
        result = await session.execute(
            update(AgentExecutionConfig)
            .where(
                AgentExecutionConfig.conversationId == conversation_id,
                AgentExecutionConfig.cancellationRequestedAt.is_(None),
                AgentExecutionConfig.taskId.in_(
                    select(DurableTask.id).where(DurableTask.status.in_(ACTIVE_STATUSES))
                ),
            )
            .values(cancellationRequestedAt=datetime.now(UTC))
        )
        return bool(type_cast(CursorResult[object], result).rowcount)


async def _latest_task_status(
    sessions: async_sessionmaker[AsyncSession], conversation_id: UUID
) -> DurableTaskStatus | None:
    """Return the current execution state without treating transport as business state."""

    async with sessions() as session:
        return type_cast(
            DurableTaskStatus | None,
            await session.scalar(
                select(DurableTask.status)
                .join(AgentExecutionConfig, AgentExecutionConfig.taskId == DurableTask.id)
                .where(AgentExecutionConfig.conversationId == conversation_id)
                .order_by(AgentExecutionConfig.createdAt.desc())
                .limit(1)
            ),
        )


__all__ = [
    "append_event",
    "event_capacity_available",
    "open_event_stream",
    "request_cancellation",
    "wire_event",
]
