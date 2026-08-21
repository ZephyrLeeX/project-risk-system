"""Deterministic Asia/Shanghai relative-time-range resolution (Agent risk_list).

The pinned clock is 2026-08-21T14:00:00+08:00 — a Friday — so the expected
windows exercise the exact business semantics fixed in ``shared.time_ranges``:
Monday-start weeks, natural-day LAST_7_DAYS, and calendar month boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from risk_platform.shared.time_ranges import (
    BUSINESS_TIMEZONE,
    RiskTimeRangePreset,
    TimeRange,
    current_week_start,
    resolve_time_range,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
PINNED_NOW = datetime(2026, 8, 21, 14, 0, 0, tzinfo=SHANGHAI)


def _shanghai(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    ("preset", "start", "end"),
    (
        (
            RiskTimeRangePreset.CURRENT_WEEK,
            _shanghai(2026, 8, 17),
            _shanghai(2026, 8, 24),
        ),
        (
            RiskTimeRangePreset.PREVIOUS_WEEK,
            _shanghai(2026, 8, 10),
            _shanghai(2026, 8, 17),
        ),
        (
            RiskTimeRangePreset.LAST_7_DAYS,
            # Natural-day window: the seven calendar days ending today.
            _shanghai(2026, 8, 15),
            _shanghai(2026, 8, 22),
        ),
        (
            RiskTimeRangePreset.CURRENT_MONTH,
            _shanghai(2026, 8, 1),
            _shanghai(2026, 9, 1),
        ),
        (
            RiskTimeRangePreset.PREVIOUS_MONTH,
            _shanghai(2026, 7, 1),
            _shanghai(2026, 8, 1),
        ),
    ),
)
def test_pinned_friday_resolves_each_preset_to_half_open_window(
    preset: RiskTimeRangePreset, start: datetime, end: datetime
) -> None:
    window = resolve_time_range(preset, PINNED_NOW)
    assert window.start == start
    assert window.end == end
    # Half-open [start, end): the boundaries are week/month anchors, never a
    # fuzzy 23:59:59.999999 end.
    assert window.start.tzinfo is not None and window.end.tzinfo is not None
    assert window.end - window.start >= timedelta(days=6)


def test_current_week_start_matches_the_preset_boundary() -> None:
    # The dashboard weeklyNewRisk* counters and the Agent CURRENT_WEEK preset
    # share one week-boundary authority.
    assert current_week_start(PINNED_NOW) == resolve_time_range(
        RiskTimeRangePreset.CURRENT_WEEK, PINNED_NOW
    ).start


@pytest.mark.parametrize(
    ("now", "preset", "start", "end"),
    (
        # Year rollover: December's current month ends on next year's Jan 1st.
        (
            datetime(2026, 12, 31, 23, 0, tzinfo=SHANGHAI),
            RiskTimeRangePreset.CURRENT_MONTH,
            _shanghai(2026, 12, 1),
            _shanghai(2027, 1, 1),
        ),
        # January's previous month crosses back over the year boundary.
        (
            datetime(2026, 1, 15, 9, 0, tzinfo=SHANGHAI),
            RiskTimeRangePreset.PREVIOUS_MONTH,
            _shanghai(2025, 12, 1),
            _shanghai(2026, 1, 1),
        ),
        # A month-start Monday: the current week starts on the 1st itself.
        (
            datetime(2026, 11, 2, 8, 0, tzinfo=SHANGHAI),
            RiskTimeRangePreset.CURRENT_WEEK,
            _shanghai(2026, 11, 2),
            _shanghai(2026, 11, 9),
        ),
        # UTC input instants still resolve on the Shanghai wall clock: Friday
        # 06:00 UTC is Friday 14:00 Shanghai.
        (
            datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
            RiskTimeRangePreset.CURRENT_WEEK,
            _shanghai(2026, 8, 17),
            _shanghai(2026, 8, 24),
        ),
    ),
)
def test_month_and_week_boundaries_roll_over_correctly(
    now: datetime, preset: RiskTimeRangePreset, start: datetime, end: datetime
) -> None:
    window = resolve_time_range(preset, now)
    assert window.start == start
    assert window.end == end


def test_naive_instants_are_rejected() -> None:
    naive = datetime(2026, 8, 21, 14, 0, 0)
    with pytest.raises(ValueError, match="aware"):
        resolve_time_range(RiskTimeRangePreset.CURRENT_WEEK, naive)
    with pytest.raises(ValueError, match="aware"):
        current_week_start(naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeRange(start=naive, end=naive + timedelta(days=1))


def test_time_range_rejects_empty_and_inverted_windows() -> None:
    aware = _shanghai(2026, 8, 17)
    with pytest.raises(ValueError, match="before"):
        TimeRange(start=aware, end=aware)
    with pytest.raises(ValueError, match="before"):
        TimeRange(start=aware + timedelta(days=1), end=aware)


def test_business_timezone_is_shanghai() -> None:
    assert str(BUSINESS_TIMEZONE) == "Asia/Shanghai"
