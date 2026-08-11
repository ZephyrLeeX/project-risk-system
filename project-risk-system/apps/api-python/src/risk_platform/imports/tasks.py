"""Registration adapter for the existing durable-task executor."""

from __future__ import annotations

from risk_platform.imports.worker import ImportPreviewWorker
from risk_platform.reliability.core import TaskHandler
from risk_platform.reliability.models import DurableTaskKind


def preview_handler(worker: ImportPreviewWorker) -> TaskHandler:
    """Return the JSON-bound handler consumed by T008/T040 executor wiring."""

    return worker.handle


def handlers(worker: ImportPreviewWorker) -> dict[str, TaskHandler]:
    """Expose only the approved import kind to the shared executor."""

    return {DurableTaskKind.IMPORT_PREVIEW.value: worker.handle}


__all__ = ["handlers", "preview_handler"]
