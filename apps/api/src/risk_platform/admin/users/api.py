"""Compatible `/api/admin/users*` routes, composed by the application assembler."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from risk_platform.admin.models import UserStatus
from risk_platform.admin.users.schemas import (
    AdminUserResponse,
    PaginatedUsersResponse,
    ProjectScopesResponse,
    SetProjectScopesRequest,
    SetUserStatusRequest,
    UserAuditRecordResponse,
    UserMutationRequest,
    UserMutationResponse,
    UserSummaryResponse,
)
from risk_platform.admin.users.service import AdminUsersService
from risk_platform.auth.api import validate_request_origin
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def get_admin_users_service(request: Request) -> AdminUsersService:
    service = getattr(request.app.state, "admin_users_service", None)
    if not isinstance(service, AdminUsersService):
        raise RuntimeError("admin user service is not configured")
    return service


@router.get("/summary", response_model=ApiResponse[UserSummaryResponse])
async def summary(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[UserSummaryResponse]:
    del identity
    return ok(request, await service.summary())


@router.get("", response_model=ApiResponse[PaginatedUsersResponse])
async def list_users(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=128)] = None,
    roleCode: Annotated[str | None, Query(max_length=64)] = None,
    status: UserStatus | None = None,
    departmentId: UUID | None = None,
) -> ApiResponse[PaginatedUsersResponse]:
    del identity
    return ok(
        request,
        await service.list_users(
            page=page,
            page_size=pageSize,
            keyword=keyword,
            role_code=roleCode,
            status=status,
            department_id=departmentId,
        ),
    )


@router.post(
    "",
    response_model=ApiResponse[UserMutationResponse],
    response_model_exclude_none=True,
    dependencies=[Depends(validate_request_origin)],
)
async def create_user(
    request: Request,
    payload: UserMutationRequest,
    identity: Annotated[
        SessionIdentity, Depends(require_permissions("admin.user.manage", "admin.scope.manage"))
    ],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[UserMutationResponse]:
    return ok(
        request,
        await service.create(payload, identity, UUID(get_trace_id(request))),
        "用户创建成功，请安全转交一次性初始密码",
    )


@router.get("/{user_id}", response_model=ApiResponse[AdminUserResponse])
async def get_user(
    request: Request,
    user_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[AdminUserResponse]:
    del identity
    return ok(request, await service.get(user_id))


@router.patch(
    "/{user_id}",
    response_model=ApiResponse[UserMutationResponse],
    response_model_exclude_none=True,
    dependencies=[Depends(validate_request_origin)],
)
async def update_user(
    request: Request,
    user_id: UUID,
    payload: UserMutationRequest,
    identity: Annotated[
        SessionIdentity, Depends(require_permissions("admin.user.manage", "admin.scope.manage"))
    ],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[UserMutationResponse]:
    return ok(
        request,
        await service.update(user_id, payload, identity, UUID(get_trace_id(request))),
        "用户信息已更新",
    )


@router.post(
    "/{user_id}/status",
    response_model=ApiResponse[AdminUserResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def set_status(
    request: Request,
    user_id: UUID,
    payload: SetUserStatusRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[AdminUserResponse]:
    status = UserStatus(payload.status)
    return ok(
        request,
        await service.set_status(user_id, status, identity, UUID(get_trace_id(request))),
        "用户已启用" if status is UserStatus.ACTIVE else "用户已停用",
    )


@router.post(
    "/{user_id}/unlock",
    response_model=ApiResponse[AdminUserResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def unlock(
    request: Request,
    user_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[AdminUserResponse]:
    return ok(
        request,
        await service.unlock(user_id, identity, UUID(get_trace_id(request))),
        "用户锁定已解除",
    )


@router.post(
    "/{user_id}/reset-password",
    response_model=ApiResponse[dict[str, str]],
    dependencies=[Depends(validate_request_origin)],
)
async def reset_password(
    request: Request,
    user_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[dict[str, str]]:
    return ok(
        request,
        {
            "initialPassword": await service.reset_password(
                user_id, identity, UUID(get_trace_id(request))
            )
        },
        "密码已重置，原有会话已撤销",
    )


@router.get("/{user_id}/records", response_model=ApiResponse[list[UserAuditRecordResponse]])
async def records(
    request: Request,
    user_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.user.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[list[UserAuditRecordResponse]]:
    del identity
    return ok(request, await service.records(user_id))


@router.get("/{user_id}/project-scopes", response_model=ApiResponse[ProjectScopesResponse])
async def get_project_scopes(
    request: Request,
    user_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.scope.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[ProjectScopesResponse]:
    del identity
    return ok(request, await service.get_project_scopes(user_id))


@router.put(
    "/{user_id}/project-scopes",
    response_model=ApiResponse[AdminUserResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def set_project_scopes(
    request: Request,
    user_id: UUID,
    payload: SetProjectScopesRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.scope.manage"))],
    service: Annotated[AdminUsersService, Depends(get_admin_users_service)],
) -> ApiResponse[AdminUserResponse]:
    return ok(
        request,
        await service.set_project_scopes(
            user_id,
            DataScopeType(payload.dataScope),
            payload.projectIds,
            identity,
            UUID(get_trace_id(request)),
        ),
        "项目数据范围已更新",
    )


__all__ = ["get_admin_users_service", "router"]
