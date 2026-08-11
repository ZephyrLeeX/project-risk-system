"""Presentation rules for risk timeline events."""

from __future__ import annotations

from dataclasses import dataclass

from risk_platform.todos.models import ActionItemStatus
from risk_platform.timeline.models import RiskTimelineEventType


@dataclass(frozen=True, slots=True)
class ActionTimelineSnapshot:
    status: ActionItemStatus
    assignee_name: str | None
    due_date: str | None
    completion_note: str | None


@dataclass(frozen=True, slots=True)
class TimelineChange:
    event_type: RiskTimelineEventType
    title: str
    description: str
    from_value: str | None = None
    to_value: str | None = None


def build_action_timeline_change(
    before: ActionTimelineSnapshot, after: ActionTimelineSnapshot
) -> TimelineChange:
    if before.status != after.status:
        completed = after.status is ActionItemStatus.COMPLETED
        return TimelineChange(
            event_type=(
                RiskTimelineEventType.ACTION_COMPLETED
                if completed
                else RiskTimelineEventType.ACTION_STATUS_CHANGED
            ),
            title="待办事项已完成" if completed else "待办处理状态更新",
            description=(
                f"待办由“{before.status.value}”变更为“{after.status.value}”"
                + (f"：{after.completion_note}" if completed and after.completion_note else "。")
            ),
            from_value=before.status.value,
            to_value=after.status.value,
        )
    changes: list[str] = []
    if before.assignee_name != after.assignee_name:
        changes.append(f"负责人由“{before.assignee_name or '待分配'}”调整为“{after.assignee_name or '待分配'}”")
    if before.due_date != after.due_date:
        changes.append(f"截止日期由“{before.due_date or '待安排'}”调整为“{after.due_date or '待安排'}”")
    if before.completion_note != after.completion_note:
        changes.append("处理说明已更新")
    return TimelineChange(
        event_type=RiskTimelineEventType.ACTION_UPDATED,
        title="待办事项信息更新",
        description=f"{'；'.join(changes)}。" if changes else "待办事项已更新。",
    )


def event_presentation(event_type: RiskTimelineEventType) -> tuple[str, str]:
    return {
        RiskTimelineEventType.RISK_CREATED: ("新增风险", "RED"),
        RiskTimelineEventType.RISK_UPDATED: ("风险更新", "BLUE"),
        RiskTimelineEventType.LEVEL_CHANGED: ("等级变化", "ORANGE"),
        RiskTimelineEventType.ACTION_CREATED: ("生成待办", "BLUE"),
        RiskTimelineEventType.ACTION_UPDATED: ("待办更新", "BLUE"),
        RiskTimelineEventType.ACTION_STATUS_CHANGED: ("处理推进", "ORANGE"),
        RiskTimelineEventType.ACTION_COMPLETED: ("待办完成", "GREEN"),
        RiskTimelineEventType.RISK_RESOLVED: ("风险解除", "GREEN"),
        RiskTimelineEventType.RISK_REOPENED: ("风险重启", "RED"),
    }[event_type]


__all__ = ["ActionTimelineSnapshot", "TimelineChange", "build_action_timeline_change", "event_presentation"]
