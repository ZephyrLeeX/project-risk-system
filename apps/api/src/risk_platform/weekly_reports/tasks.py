"""Weekly-report durable-task registration."""

from __future__ import annotations

from risk_platform.reliability.core import TaskHandler
from risk_platform.reliability.models import DurableTaskKind

from .service import WeeklyReportService


def handlers(service: WeeklyReportService) -> dict[str, TaskHandler]:
    return {DurableTaskKind.WEEKLY_REPORT_REBUILD.value: service.handle}


__all__ = ["handlers"]
