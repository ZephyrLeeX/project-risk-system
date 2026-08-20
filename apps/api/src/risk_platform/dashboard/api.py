"""Dashboard summary and focus HTTP routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.risks.schemas import RiskItem
from risk_platform.shared.http import ApiResponse, ok

from .schemas import (
    CollectionQuery,
    DashboardSummary,
    DepartmentCollectionDetail,
    DepartmentCollectionSummary,
    RiskCollectionDetail,
    RiskCollectionListResponse,
)
from .service import DashboardService

router = APIRouter(tags=["dashboard"])


def get_dashboard_service(request: Request) -> DashboardService:
    service = getattr(request.app.state, "dashboard_service", None)
    if not isinstance(service, DashboardService):
        raise RuntimeError("dashboard service is not configured")
    return service


@router.get("/dashboard/summary", response_model=ApiResponse[DashboardSummary])
async def summary(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> ApiResponse[DashboardSummary]:
    return ok(request, await service.summary(identity))


@router.get("/dashboard/focus", response_model=ApiResponse[list[RiskItem]])
async def focus(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> ApiResponse[list[RiskItem]]:
    return ok(request, await service.focus(identity))


@router.get(
    "/dashboard/departments/collections", response_model=ApiResponse[DepartmentCollectionSummary]
)
async def department_collections(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> ApiResponse[DepartmentCollectionSummary]:
    return ok(request, await service.department_collections(identity))


@router.get(
    "/dashboard/departments/{department_key}/collections",
    response_model=ApiResponse[DepartmentCollectionDetail],
)
async def department_collection_detail(
    request: Request,
    department_key: str,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> ApiResponse[DepartmentCollectionDetail]:
    return ok(request, await service.department_collection_detail(identity, department_key))


@router.get("/dashboard/collections", response_model=ApiResponse[RiskCollectionListResponse])
async def risk_collections(
    request: Request,
    query: Annotated[CollectionQuery, Depends()],
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> ApiResponse[RiskCollectionListResponse]:
    return ok(request, await service.risk_collections(identity, query))


@router.get("/dashboard/collections/{project_id}", response_model=ApiResponse[RiskCollectionDetail])
async def risk_collection_detail(
    request: Request,
    project_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> ApiResponse[RiskCollectionDetail]:
    return ok(request, await service.risk_collection_detail(identity, project_id))


__all__ = ["get_dashboard_service", "router"]
