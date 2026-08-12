"""Mailbox durable-task registrations."""

from __future__ import annotations

from risk_platform.mailbox.parse_worker import MailParseWorker
from risk_platform.mailbox.sync import MailboxSyncService, sync_handler
from risk_platform.reliability.core import TaskHandler
from risk_platform.reliability.models import DurableTaskKind


def handlers(
    service: MailboxSyncService, parser: MailParseWorker | None = None
) -> dict[str, TaskHandler]:
    handler = sync_handler(service)
    result: dict[str, TaskHandler] = {
        DurableTaskKind.MAILBOX_SYNC.value: handler,
    }
    if parser is not None:
        result[DurableTaskKind.ATTACHMENT_PARSE.value] = parser.handle
        result[DurableTaskKind.MAIL_MESSAGE_RETRY.value] = parser.handle
    return result


__all__ = ["handlers"]
