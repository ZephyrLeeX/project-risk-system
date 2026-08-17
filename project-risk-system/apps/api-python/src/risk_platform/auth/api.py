"""FastAPI authentication routes and reusable session dependencies."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from risk_platform.auth.schemas import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    SessionResponse,
)
from risk_platform.auth.service import AuthService, RequestContext, SessionIdentity
from risk_platform.config import Settings
from risk_platform.shared.errors import ApiError
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.security import session_cookie_options
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if not isinstance(service, AuthService):
        raise RuntimeError("authentication service is not configured")
    return service


def validate_request_origin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.request_origin_validation_enabled:
        return
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        raise ApiError(403, "FORBIDDEN", "请求来源校验失败")


async def current_identity(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> SessionIdentity:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    trace_id = UUID(get_trace_id(request))
    if token is None:
        await service.record_missing_session(trace_id=trace_id)
        raise ApiError(
            401,
            "UNAUTHORIZED",
            "登录状态已失效，请重新登录",  # noqa: RUF001
            headers={"WWW-Authenticate": "Session"},
        )
    return await service.authenticate(token, trace_id=trace_id)


async def require_password_changed(
    identity: Annotated[SessionIdentity, Depends(current_identity)],
) -> SessionIdentity:
    if identity.user.mustChangePassword:
        raise ApiError(403, "FORBIDDEN", "请先修改初始密码")
    return identity


@router.post(
    "/login",
    response_model=ApiResponse[SessionResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[SessionResponse]:
    result = await service.login(
        username=payload.username,
        password=payload.password,
        context=_request_context(request),
        trace_id=UUID(get_trace_id(request)),
    )
    settings: Settings = request.app.state.settings
    response.set_cookie(
        settings.session_cookie_name,
        result.token,
        expires=result.expires_at,
        **session_cookie_options(settings),
    )
    message = (
        "登录成功，请先修改初始密码"  # noqa: RUF001
        if result.user.mustChangePassword
        else "登录成功"
    )
    return ok(
        request,
        SessionResponse(
            user=result.user,
            expiresAt=service.format_expiration(result.expires_at),
        ),
        message,
    )


@router.get("/session", response_model=ApiResponse[SessionResponse])
async def get_session(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
    identity: Annotated[SessionIdentity, Depends(current_identity)],
) -> ApiResponse[SessionResponse]:
    return ok(
        request,
        SessionResponse(
            user=identity.user,
            expiresAt=service.format_expiration(identity.expires_at),
        ),
        "会话有效",
    )


@router.post(
    "/change-password",
    response_model=ApiResponse[ChangePasswordResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def change_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
    identity: Annotated[SessionIdentity, Depends(current_identity)],
) -> ApiResponse[ChangePasswordResponse]:
    await service.change_password(
        identity,
        current_password=payload.currentPassword,
        new_password=payload.newPassword,
        confirm_password=payload.confirmPassword,
        trace_id=UUID(get_trace_id(request)),
    )
    _clear_cookie(request, response)
    return ok(
        request,
        ChangePasswordResponse(),
        "密码修改成功，请使用新密码重新登录",  # noqa: RUF001
    )


@router.post(
    "/logout",
    response_model=ApiResponse[None],
    dependencies=[Depends(validate_request_origin)],
)
async def logout(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    identity: Annotated[SessionIdentity, Depends(current_identity)],
) -> ApiResponse[None]:
    await service.logout(identity, trace_id=UUID(get_trace_id(request)))
    _clear_cookie(request, response)
    return ok(request, None, "已安全退出")


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        client_ip=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("user-agent"),
    )


def _clear_cookie(request: Request, response: Response) -> None:
    settings: Settings = request.app.state.settings
    response.delete_cookie(
        settings.session_cookie_name,
        **session_cookie_options(settings),
    )


__all__ = [
    "current_identity",
    "get_auth_service",
    "require_password_changed",
    "router",
    "validate_request_origin",
]
