from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.agent import events
from risk_platform.reliability.models import DurableTaskStatus


class _EmptyResult:
    def all(self) -> list[object]:
        return []


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalars(self, _statement: object) -> _EmptyResult:
        return _EmptyResult()


class _Sessions:
    def __call__(self) -> _Session:
        return _Session()


def test_running_stream_sends_transport_keepalive_beyond_idle_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses: Iterator[DurableTaskStatus | None] = iter(
        [DurableTaskStatus.RUNNING] * 3 + [DurableTaskStatus.SUCCEEDED]
    )
    monkeypatch.setattr(events, "_latest_task_status", AsyncMock(side_effect=statuses))
    cancellation = AsyncMock()
    monkeypatch.setattr(events, "request_cancellation", cancellation)

    async def collect() -> list[bytes]:
        stream = events._stream(
            cast(async_sessionmaker[AsyncSession], _Sessions()),
            uuid4(),
            0,
            poll_interval=0,
            idle_seconds=0,
            keepalive_seconds=0,
        )
        frames = [await anext(stream), await anext(stream), await anext(stream)]
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        return frames

    frames = asyncio.run(collect())
    assert frames == [b": keepalive\n\n"] * 3
    cancellation.assert_not_awaited()


def test_retry_wait_uses_the_same_non_persistent_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses: Iterator[DurableTaskStatus | None] = iter(
        [DurableTaskStatus.RETRY_WAIT, DurableTaskStatus.SUCCEEDED]
    )
    monkeypatch.setattr(events, "_latest_task_status", AsyncMock(side_effect=statuses))

    async def collect() -> bytes:
        stream = events._stream(
            cast(async_sessionmaker[AsyncSession], _Sessions()),
            uuid4(),
            0,
            poll_interval=0,
            idle_seconds=60,
            keepalive_seconds=0,
        )
        return await anext(stream)

    assert asyncio.run(collect()) == b": keepalive\n\n"


def test_abnormal_disconnect_does_not_cancel_the_durable_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A browser refresh / tab close / network drop stops iterating mid-stream
    # (generator close, no terminal event seen).  The durable execution must
    # keep running and self-terminate via its own timeout/lease; only an
    # explicit POST /cancel calls request_cancellation.  The stream's cleanup
    # path must therefore NOT touch business state.
    monkeypatch.setattr(
        events,
        "_latest_task_status",
        AsyncMock(side_effect=iter([DurableTaskStatus.RUNNING])),
    )
    cancellation = AsyncMock()
    monkeypatch.setattr(events, "request_cancellation", cancellation)

    async def close_mid_stream() -> bytes:
        stream = events._stream(
            cast(async_sessionmaker[AsyncSession], _Sessions()),
            uuid4(),
            0,
            poll_interval=0,
            idle_seconds=0,
            keepalive_seconds=0,
        )
        frame = await anext(stream)
        await stream.aclose()
        return frame

    assert asyncio.run(close_mid_stream()) == b": keepalive\n\n"
    cancellation.assert_not_awaited()
