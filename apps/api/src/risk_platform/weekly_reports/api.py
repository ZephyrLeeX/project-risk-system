"""Authorized weekly-report summary and detail routes."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok

from .schemas import WeeklyProjectDetail, WeeklyReportResponse
from .service import WeeklyReportService

router = APIRouter(tags=["weekly-reports"])


def get_weekly_report_service(request: Request) -> WeeklyReportService:
    service = getattr(request.app.state, "weekly_report_service", None)
    if not isinstance(service, WeeklyReportService):
        raise RuntimeError("weekly report service is not configured")
    return service


@router.get("/weekly-reports/current", response_model=ApiResponse[WeeklyReportResponse])
async def current(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[WeeklyReportService, Depends(get_weekly_report_service)],
) -> ApiResponse[WeeklyReportResponse]:
    return ok(request, await service.current(identity))


@router.get("/weekly-reports/{week_start}", response_model=ApiResponse[WeeklyReportResponse])
async def report(
    request: Request,
    week_start: date,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[WeeklyReportService, Depends(get_weekly_report_service)],
) -> ApiResponse[WeeklyReportResponse]:
    return ok(request, await service.report(identity, week_start))


@router.get(
    "/weekly-reports/{week_start}/projects/{project_id}",
    response_model=ApiResponse[WeeklyProjectDetail],
)
async def detail(
    request: Request,
    week_start: date,
    project_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[WeeklyReportService, Depends(get_weekly_report_service)],
) -> ApiResponse[WeeklyProjectDetail]:
    return ok(request, await service.detail(identity, week_start, project_id))


__all__ = ["get_weekly_report_service", "router"]
