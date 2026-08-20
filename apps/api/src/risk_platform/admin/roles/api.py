"""Compatible `/api/admin` role, permission and department routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.admin.roles.schemas import (
    CreateRoleRequest,
    PermissionResponse,
    RoleResponse,
    UpdateRoleRequest,
)
from risk_platform.admin.roles.service import AdminRolesService
from risk_platform.auth.api import validate_request_origin
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin", tags=["admin-roles"])


def get_admin_roles_service(request: Request) -> AdminRolesService:
    service = getattr(request.app.state, "admin_roles_service", None)
    if not isinstance(service, AdminRolesService):
        raise RuntimeError("admin role service is not configured")
    return service


@router.get("/roles", response_model=ApiResponse[list[RoleResponse]])
async def list_roles(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.role.manage"))],
    service: Annotated[AdminRolesService, Depends(get_admin_roles_service)],
) -> ApiResponse[list[RoleResponse]]:
    del identity
    return ok(request, await service.list_roles())


@router.post(
    "/roles",
    response_model=ApiResponse[RoleResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def create_role(
    request: Request,
    payload: CreateRoleRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.role.manage"))],
    service: Annotated[AdminRolesService, Depends(get_admin_roles_service)],
) -> ApiResponse[RoleResponse]:
    return ok(
        request,
        await service.create(payload, identity, UUID(get_trace_id(request))),
        "角色创建成功",
    )


@router.patch(
    "/roles/{role_id}",
    response_model=ApiResponse[RoleResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def update_role(
    request: Request,
    role_id: UUID,
    payload: UpdateRoleRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.role.manage"))],
    service: Annotated[AdminRolesService, Depends(get_admin_roles_service)],
) -> ApiResponse[RoleResponse]:
    return ok(
        request,
        await service.update(role_id, payload, identity, UUID(get_trace_id(request))),
        "角色权限已保存并立即生效",
    )


@router.delete(
    "/roles/{role_id}",
    response_model=ApiResponse[None],
    dependencies=[Depends(validate_request_origin)],
)
async def delete_role(
    request: Request,
    role_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.role.manage"))],
    service: Annotated[AdminRolesService, Depends(get_admin_roles_service)],
) -> ApiResponse[None]:
    await service.remove(role_id, identity, UUID(get_trace_id(request)))
    return ok(request, None, "角色已删除")


@router.get("/permissions", response_model=ApiResponse[list[PermissionResponse]])
async def list_permissions(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.role.manage"))],
    service: Annotated[AdminRolesService, Depends(get_admin_roles_service)],
) -> ApiResponse[list[PermissionResponse]]:
    del identity
    return ok(request, await service.list_permissions())


__all__ = ["get_admin_roles_service", "router"]
