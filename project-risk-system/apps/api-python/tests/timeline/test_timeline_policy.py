from risk_platform.todos.models import ActionItemStatus  # noqa: I001
from risk_platform.timeline.models import RiskTimelineEventType
from risk_platform.timeline.policy import (
    ActionTimelineSnapshot,
    build_action_timeline_change,
    event_presentation,
)


def snapshot(status: ActionItemStatus, *, assignee: str | None = "张三") -> ActionTimelineSnapshot:
    return ActionTimelineSnapshot(
        status=status, assignee_name=assignee, due_date=None, completion_note=None
    )


def test_status_change_uses_completed_event_only_for_completed_state() -> None:
    completed = build_action_timeline_change(
        snapshot(ActionItemStatus.IN_PROGRESS), snapshot(ActionItemStatus.COMPLETED)
    )
    assert completed.event_type is RiskTimelineEventType.ACTION_COMPLETED
    assert completed.from_value == "IN_PROGRESS"
    progressed = build_action_timeline_change(
        snapshot(ActionItemStatus.PENDING), snapshot(ActionItemStatus.IN_PROGRESS)
    )
    assert progressed.event_type is RiskTimelineEventType.ACTION_STATUS_CHANGED


def test_metadata_changes_are_recorded_as_action_update() -> None:
    before = snapshot(ActionItemStatus.PENDING, assignee=None)
    after = ActionTimelineSnapshot(ActionItemStatus.PENDING, "李四", "2026-08-20", "说明")
    change = build_action_timeline_change(before, after)
    assert change.event_type is RiskTimelineEventType.ACTION_UPDATED
    assert "负责人" in change.description
    assert event_presentation(RiskTimelineEventType.RISK_RESOLVED) == ("风险解除", "GREEN")
