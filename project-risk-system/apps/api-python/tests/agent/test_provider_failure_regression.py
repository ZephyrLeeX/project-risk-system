from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.reliability import dispatcher
from risk_platform.reliability.dispatcher import DurableTaskFailure
from risk_platform.reliability.models import DurableTaskKind, DurableTaskStatus


class _Transaction:
    async def __aenter__(self) -> _Transaction:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Session:
    def __init__(self, task: object) -> None:
        self.task = task

    def begin(self) -> _Transaction:
        return _Transaction()

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _model: object, _task_id: object) -> object:
        return self.task


class _Sessions:
    def __init__(self, task: object) -> None:
        self.task = task
        self.opened: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session(self.task)
        self.opened.append(session)
        return session


class _ProviderFailureHandler:
    with_context = False

    def __init__(self) -> None:
        self.finalized: list[tuple[object, object, str]] = []

    async def __call__(self, _payload: object) -> None:
        raise DurableTaskFailure(
            "AGENT_PROVIDER_UNAVAILABLE",
            retryable=True,
            summary="provider candidates unavailable",
        )

    async def finalize_task_failure(self, session: object, task_id: object, code: str) -> None:
        self.finalized.append((session, task_id, code))


@pytest.mark.parametrize(
    ("finished_status", "expected_finalizers"),
    [
        (DurableTaskStatus.RETRY_WAIT, 0),
        (DurableTaskStatus.FAILED, 1),
    ],
)
def test_provider_failure_terminalizes_only_after_durable_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
    finished_status: DurableTaskStatus,
    expected_finalizers: int,
) -> None:
    task_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        kind=DurableTaskKind.AGENT_EXECUTION,
        payload={},
        attemptCount=3,
    )
    sessions = _Sessions(task)
    handler = _ProviderFailureHandler()
    statuses = iter([finished_status])

    async def claim(_session: object, _task_id: object, _generation: int, _owner: str) -> object:
        return uuid4()

    async def finish(*_args: object, **_kwargs: object) -> DurableTaskStatus:
        return next(statuses)

    monkeypatch.setattr(dispatcher, "claim_task", claim)
    monkeypatch.setattr(dispatcher, "finish_task", finish)

    asyncio.run(
        dispatcher.execute_message(
            cast(async_sessionmaker[AsyncSession], sessions),
            object(),
            task_id,
            1,
            owner="test",
            handlers={DurableTaskKind.AGENT_EXECUTION.value: handler},
        )
    )

    assert len(handler.finalized) == expected_finalizers
    if expected_finalizers:
        assert handler.finalized[0][1:] == (task_id, "AGENT_PROVIDER_UNAVAILABLE")
