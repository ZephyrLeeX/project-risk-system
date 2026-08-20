from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from risk_platform.reliability.celery_app import create_celery_app
from risk_platform.reliability.dispatcher import publish_outbox
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.reliability.registry import TASK_REGISTRY


class Broker:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, list[object]]] = []

    def send_task(self, name: str, *, args: list[object]) -> None:
        if self.fail:
            raise ConnectionError("broker unavailable")
        self.messages.append((name, args))


def test_outbox_is_not_marked_published_when_broker_is_unavailable() -> None:
    row = SimpleNamespace(taskId=uuid4(), dispatchGeneration=1, publishedAt=None)
    result = SimpleNamespace(all=lambda: [row])
    session = SimpleNamespace(scalars=AsyncMock(return_value=result))

    with pytest.raises(ConnectionError):
        asyncio.run(publish_outbox(session, Broker(fail=True)))  # type: ignore[arg-type]

    assert row.publishedAt is None


def test_outbox_message_contains_only_task_id_and_generation() -> None:
    row = SimpleNamespace(taskId=uuid4(), dispatchGeneration=3, publishedAt=None)
    result = SimpleNamespace(all=lambda: [row])
    session = SimpleNamespace(scalars=AsyncMock(return_value=result))
    broker = Broker()

    assert asyncio.run(publish_outbox(session, broker)) == 1  # type: ignore[arg-type]
    assert broker.messages == [("risk_platform.reliability.execute", [str(row.taskId), 3])]
    assert row.publishedAt is not None


def test_registry_is_closed_and_celery_does_not_use_result_backend() -> None:
    assert set(TASK_REGISTRY) == set(DurableTaskKind)
    assert create_celery_app().conf.result_backend is None
