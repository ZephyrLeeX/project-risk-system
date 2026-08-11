"""Mailbox durable-task registrations."""

from __future__ import annotations

from risk_platform.mailbox.sync import MailboxSyncService, sync_handler
from risk_platform.reliability.core import TaskHandler
from risk_platform.reliability.models import DurableTaskKind


def handlers(service: MailboxSyncService) -> dict[str, TaskHandler]:
    handler = sync_handler(service)
    return {
        DurableTaskKind.MAILBOX_SYNC.value: handler,
    }


__all__ = ["handlers"]
