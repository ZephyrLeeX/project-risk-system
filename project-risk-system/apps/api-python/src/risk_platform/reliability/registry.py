"""The deliberately closed registry of durable task kinds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from risk_platform.reliability.models import DurableTaskKind


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    kind: DurableTaskKind
    max_attempts: int = 3
    timeout_seconds: int = 900
    retry_backoff_seconds: int = 30


TASK_REGISTRY: Final[dict[DurableTaskKind, TaskDefinition]] = {
    kind: TaskDefinition(kind) for kind in DurableTaskKind
}


def task_definition(kind: DurableTaskKind) -> TaskDefinition:
    """Return an approved definition; arbitrary task kinds are not accepted."""

    return TASK_REGISTRY[kind]


__all__ = ["TASK_REGISTRY", "TaskDefinition", "task_definition"]
