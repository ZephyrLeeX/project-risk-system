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
    AgentMessage,
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
        and int(payload_bytes) + len(encoded)
        <= MAX_PENDING_BYTES - TERMINAL_EVENT_RESERVE_BYTES
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
    )


async def _stream(
    sessions: async_sessionmaker[AsyncSession],
    conversation_id: UUID,
    after_sequence: int,
    *,
    poll_interval: float,
    idle_seconds: float,
) -> AsyncIterator[bytes]:
    terminal_seen = False
    normal_close = False
    loop = asyncio.get_running_loop()
    last_fact_at = loop.time()
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
                    terminal_seen = row.type in TERMINAL_EVENT_TYPES
                    yield wire_event(row)
                last_fact_at = loop.time()
                if terminal_seen:
                    normal_close = True
                    return
                continue
            if loop.time() - last_fact_at >= idle_seconds:
                idle_event = await _append_idle_timeout(sessions, conversation_id)
                if idle_event is not None:
                    yield wire_event(idle_event)
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


async def _append_idle_timeout(
    sessions: async_sessionmaker[AsyncSession], conversation_id: UUID
) -> AgentEvent | None:
    async with sessions.begin() as session:
        row = await session.execute(
            select(AgentExecutionConfig, DurableTask)
            .join(DurableTask, DurableTask.id == AgentExecutionConfig.taskId)
            .where(
                AgentExecutionConfig.conversationId == conversation_id,
                DurableTask.status.in_(ACTIVE_STATUSES),
            )
            .order_by(AgentExecutionConfig.createdAt.desc())
            .with_for_update(of=DurableTask)
            .limit(1)
        )
        pair = row.one_or_none()
        if pair is None:
            return None
        config, task = pair
        existing = await session.scalar(
            select(AgentEvent.id).where(
                AgentEvent.taskId == task.id,
                AgentEvent.type == AgentEventType.ERROR,
                AgentEvent.payload["code"].as_string() == "AGENT_STREAM_IDLE_TIMEOUT",
            )
        )
        if existing is not None:
            return None
        trace_id = await session.scalar(
            select(AgentMessage.traceId).where(AgentMessage.id == config.userMessageId)
        )
        if trace_id is None:
            return None
        payload = {
            "code": "AGENT_STREAM_IDLE_TIMEOUT",
            "message": "Agent事件流空闲超时; 请使用会话记录恢复",
            "retryable": True,
            "traceId": trace_id,
        }
        if not await event_capacity_available(session, conversation_id, payload):
            return None
        return await append_event(
            session,
            conversation_id=conversation_id,
            message_id=config.userMessageId,
            task_id=task.id,
            event_type=AgentEventType.ERROR,
            payload=payload,
        )


__all__ = [
    "append_event",
    "event_capacity_available",
    "open_event_stream",
    "request_cancellation",
    "wire_event",
]
