"""Project status policy boundary.

The existing repository has import lifecycle status writes but no approved
user-facing Project status transition authority. T051 therefore exposes the
central boundary and fails closed until a transition matrix is approved.
"""

from __future__ import annotations

from risk_platform.projects.models import ProjectStatus
from risk_platform.shared.errors import ApiError


class ProjectStatusPolicy:
    allowed_statuses = frozenset(ProjectStatus)

    @classmethod
    def validate(cls, current: ProjectStatus, target: ProjectStatus) -> None:
        del current, target
        raise ApiError(409, "DESIGN_GAP", "Project status transition policy尚未批准")


__all__ = ["ProjectStatusPolicy"]
