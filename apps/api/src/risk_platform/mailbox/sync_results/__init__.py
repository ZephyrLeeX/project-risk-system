"""T043 mailbox sync-results browse/retry surface."""

from risk_platform.mailbox.sync_results.api import router
from risk_platform.mailbox.sync_results.service import MailSyncResultsService

__all__ = ["MailSyncResultsService", "router"]
