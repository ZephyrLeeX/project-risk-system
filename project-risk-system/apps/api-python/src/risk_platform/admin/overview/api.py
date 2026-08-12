"""`GET /api/admin/overview` endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from risk_platform.admin.overview.schemas import AdminOverview
from risk_platform.admin.overview.service import AdminOverviewService
from risk_platform.auth.api import current_identity
from risk_platform.auth.service import SessionIdentity
from risk_platform.shared.http import ApiResponse, ok

router = APIRouter(prefix="/admin", tags=["admin-overview"])


def get_admin_overview_service(request: Request) -> AdminOverviewService:
    service = getattr(request.app.state, "admin_overview_service", None)
    if not isinstance(service, AdminOverviewService):
        raise RuntimeError("admin overview service is not configured")
    return service


@router.get("/overview", response_model=ApiResponse[AdminOverview])
async def overview(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(current_identity)],
    service: Annotated[AdminOverviewService, Depends(get_admin_overview_service)],
) -> ApiResponse[AdminOverview]:
    return ok(request, await service.overview(identity))


__all__ = ["get_admin_overview_service", "router"]
