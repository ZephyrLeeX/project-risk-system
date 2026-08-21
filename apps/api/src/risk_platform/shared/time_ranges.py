"""Deterministic business-time range resolution (Asia/Shanghai).

The single server-side authority that turns a *relative* time expression
(``本周`` / ``上周`` / ``最近 7 天`` / ``本月`` / ``上个月``) into a concrete
half-open ``[start, end)`` instant range.  The Agent tool contract exposes the
closed ``RiskTimeRangePreset`` enum so the model only *selects* a preset — the
absolute datetime arithmetic (timezone, week start, month boundaries) is
computed here, deterministically, never by the LLM.

Semantics (fixed, Asia/Shanghai wall clock, weeks start Monday — the same
convention as ``weekly_reports.service.shanghai_week_start``):

* ``CURRENT_WEEK``   — Monday 00:00 of the current Shanghai week → +7 days
* ``PREVIOUS_WEEK``  — Monday 00:00 of the previous Shanghai week → +7 days
* ``LAST_7_DAYS``    — calendar-day window: today 00:00 minus 6 days → tomorrow
  00:00 (the seven *natural days* ending today, Shanghai)
* ``CURRENT_MONTH``  — 1st 00:00 of the current Shanghai month → 1st of next
* ``PREVIOUS_MONTH`` — 1st 00:00 of the previous Shanghai month → 1st of this

``Risk.detectedAt`` is the authoritative "risk added" timestamp on every write
path (manual, agent proposal commit, mailbox confirmation, Excel import — none
of them override it, so it equals the creation instant), and the dashboard's
``weeklyNewRiskTotal`` / ``weeklyNewHighRiskTotal`` count on it too.  Callers
must inject the ``now`` instant (a clock callable) instead of calling
``datetime.now`` inline, so tests can pin the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


class RiskTimeRangePreset(StrEnum):
    """Closed server-side relative-time presets for the Agent risk_list tool."""

    CURRENT_WEEK = "CURRENT_WEEK"
    PREVIOUS_WEEK = "PREVIOUS_WEEK"
    LAST_7_DAYS = "LAST_7_DAYS"
    CURRENT_MONTH = "CURRENT_MONTH"
    PREVIOUS_MONTH = "PREVIOUS_MONTH"


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A half-open ``[start, end)`` instant range (both timezone-aware)."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"TimeRange.{name} must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("TimeRange.start must be before TimeRange.end")


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("relative time range resolution requires an aware instant")
    return now


def _local_midnight(year: int, month: int, day: int) -> datetime:
    """Shanghai wall-clock midnight expressed as an aware UTC instant."""

    return datetime(year, month, day, tzinfo=BUSINESS_TIMEZONE).astimezone(UTC)


def resolve_time_range(preset: RiskTimeRangePreset, now: datetime) -> TimeRange:
    """Resolve one preset against ``now`` (aware) into a ``[start, end)`` range."""

    _require_aware(now)
    local = now.astimezone(BUSINESS_TIMEZONE)
    if preset is RiskTimeRangePreset.CURRENT_WEEK:
        week_start = local.date() - timedelta(days=local.weekday())
        start = _local_midnight(week_start.year, week_start.month, week_start.day)
        return TimeRange(start=start, end=start + timedelta(days=7))
    if preset is RiskTimeRangePreset.PREVIOUS_WEEK:
        week_start = local.date() - timedelta(days=local.weekday() + 7)
        start = _local_midnight(week_start.year, week_start.month, week_start.day)
        return TimeRange(start=start, end=start + timedelta(days=7))
    if preset is RiskTimeRangePreset.LAST_7_DAYS:
        start = _local_midnight(
            local.year, local.month, local.day
        ) - timedelta(days=6)
        return TimeRange(start=start, end=start + timedelta(days=7))
    if preset is RiskTimeRangePreset.CURRENT_MONTH:
        start = _local_midnight(local.year, local.month, 1)
        if local.month == 12:
            end = _local_midnight(local.year + 1, 1, 1)
        else:
            end = _local_midnight(local.year, local.month + 1, 1)
        return TimeRange(start=start, end=end)
    if preset is RiskTimeRangePreset.PREVIOUS_MONTH:
        if local.month == 1:
            start = _local_midnight(local.year - 1, 12, 1)
            end = _local_midnight(local.year, 1, 1)
        else:
            start = _local_midnight(local.year, local.month - 1, 1)
            end = _local_midnight(local.year, local.month, 1)
        return TimeRange(start=start, end=end)
    raise ValueError(f"unsupported time range preset: {preset}")


def current_week_start(now: datetime) -> datetime:
    """The current Shanghai week's Monday 00:00 as an aware UTC instant.

    The single week-boundary authority shared by the Agent time-range presets
    and the dashboard ``weeklyNewRiskTotal`` / ``weeklyNewHighRiskTotal``
    counters, so "本周新增风险" means the same window everywhere.
    """

    _require_aware(now)
    local = now.astimezone(BUSINESS_TIMEZONE)
    week_start = local.date() - timedelta(days=local.weekday())
    return _local_midnight(week_start.year, week_start.month, week_start.day)


__all__ = [
    "BUSINESS_TIMEZONE",
    "RiskTimeRangePreset",
    "TimeRange",
    "current_week_start",
    "resolve_time_range",
]
