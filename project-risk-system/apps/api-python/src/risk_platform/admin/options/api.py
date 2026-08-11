"""Compatible `/api/admin/departments` option route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from risk_platform.admin.options.schemas import DepartmentResponse
from risk_platform.admin.options.service import AdminOptionsService
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok

router = APIRouter(prefix="/admin", tags=["admin-options"])


def get_admin_options_service(request: Request) -> AdminOptionsService:
    service = getattr(request.app.state, "admin_options_service", None)
    if not isinstance(service, AdminOptionsService):
        raise RuntimeError("admin options service is not configured")
    return service


@router.get("/departments", response_model=ApiResponse[list[DepartmentResponse]])
async def list_departments(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminOptionsService, Depends(get_admin_options_service)],
) -> ApiResponse[list[DepartmentResponse]]:
    del identity
    return ok(request, await service.list_departments())


__all__ = ["get_admin_options_service", "router"]
