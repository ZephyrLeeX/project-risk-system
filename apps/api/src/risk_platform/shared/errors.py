"""Safe exception types and compatibility envelope handlers."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from risk_platform.shared.tracing import get_trace_id

logger = logging.getLogger(__name__)

_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    status.HTTP_400_BAD_REQUEST: ("BAD_REQUEST", "请求无效"),
    status.HTTP_401_UNAUTHORIZED: ("UNAUTHORIZED", "未认证"),
    status.HTTP_403_FORBIDDEN: ("FORBIDDEN", "无权限访问"),
    status.HTTP_404_NOT_FOUND: ("NOT_FOUND", "请求的资源不存在"),
    status.HTTP_405_METHOD_NOT_ALLOWED: ("METHOD_NOT_ALLOWED", "请求方法不受支持"),
    status.HTTP_409_CONFLICT: ("CONFLICT", "请求与当前状态冲突"),
}


class ApiError(Exception):
    """An intentional, client-safe API error."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        data: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.data = data
        self.headers = headers


def _response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    data: object | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    trace_id = get_trace_id(request)
    response_headers = dict(headers or {})
    response_headers["X-Trace-ID"] = trace_id
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "data": data,
            "traceId": trace_id,
        },
        headers=response_headers,
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return _response(
        request,
        exc.status_code,
        exc.code,
        exc.message,
        data=exc.data,
        headers=exc.headers,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del exc  # Input values and validation internals must not escape through the API.
    return _response(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "VALIDATION_ERROR",
        "请求参数校验失败",
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = _HTTP_ERRORS.get(exc.status_code, ("HTTP_ERROR", "请求失败"))
    return _response(request, exc.status_code, code, message, headers=exc.headers)


class SafeExceptionMiddleware:
    """Consume ordinary unhandled exceptions before the ASGI server can log them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            del exc  # Never log raw exception content because it may contain secrets.
            request = Request(scope)
            trace_id = get_trace_id(request)
            logger.error("未处理的 HTTP 异常 traceId=%s", trace_id)
            if response_started:
                raise RuntimeError(f"HTTP response failed traceId={trace_id}") from None
            response = _response(
                request,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "INTERNAL_SERVER_ERROR",
                "服务内部错误",
            )
            await response(scope, receive, send)


def install_exception_handlers(app: FastAPI) -> None:
    """Install the shared exception-to-envelope mapping."""

    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]


__all__ = ["ApiError", "SafeExceptionMiddleware", "install_exception_handlers"]
