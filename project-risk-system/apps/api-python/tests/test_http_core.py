from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from ipaddress import ip_network
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from risk_platform.app import AppComposition, create_app
from risk_platform.config import Settings, SettingsError
from risk_platform.shared.errors import ApiError
from risk_platform.shared.http import ApiResponse, StrictRequestModel, ok
from risk_platform.shared.security import session_cookie_options

_UNSET = object()


async def _async_request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    client: tuple[str, int] = ("127.0.0.1", 50000),
    raise_app_exceptions: bool = True,
    headers: Mapping[str, str] | None = None,
    json: object = _UNSET,
) -> httpx2.Response:
    transport = httpx2.ASGITransport(
        app=app,
        client=client,
        raise_app_exceptions=raise_app_exceptions,
    )
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=transport, base_url="http://testserver") as session,
    ):
        if json is _UNSET:
            return await session.request(method, path, headers=headers)
        return await session.request(method, path, headers=headers, json=json)


def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    client: tuple[str, int] = ("127.0.0.1", 50000),
    raise_app_exceptions: bool = True,
    headers: Mapping[str, str] | None = None,
    json: object = _UNSET,
) -> httpx2.Response:
    return asyncio.run(
        _async_request(
            app,
            method,
            path,
            client=client,
            raise_app_exceptions=raise_app_exceptions,
            headers=headers,
            json=json,
        )
    )


def test_liveness_uses_api_envelope_and_one_trace() -> None:
    app = create_app(Settings(environment="test"))
    response = request(app, "GET", "/api/health")

    assert response.status_code == 200
    assert response.json().keys() == {"code", "message", "data", "traceId"}
    assert response.json()["code"] == "OK"
    assert response.json()["message"] == "服务运行正常"
    assert response.json()["data"]["service"] == "project-risk-api"
    assert response.json()["data"]["status"] == "ok"
    assert response.json()["data"]["timestamp"].endswith("Z")
    assert UUID(response.json()["traceId"])
    assert response.headers["x-trace-id"] == response.json()["traceId"]


def test_valid_incoming_trace_is_reused_and_invalid_trace_is_replaced() -> None:
    app = create_app(Settings(environment="test"))
    trace_id = "4cfb42fb-3e2a-4d87-9fdd-80ae08544355"

    reused = request(app, "GET", "/api/health", headers={"x-trace-id": trace_id})
    replaced = request(app, "GET", "/api/health", headers={"x-trace-id": "not-a-uuid"})

    assert reused.json()["traceId"] == trace_id
    assert replaced.json()["traceId"] != "not-a-uuid"
    assert UUID(replaced.json()["traceId"])


class StrictInput(StrictRequestModel):
    name: str


class CancellationProbe(BaseException):
    """Represents cancellation-like control flow that catch-all must not consume."""


def _error_router() -> APIRouter:
    router = APIRouter(prefix="/contract")

    @router.post("/validate", response_model=ApiResponse[dict[str, str]])
    async def validate(request: Request, payload: StrictInput) -> ApiResponse[dict[str, str]]:
        return ok(request, {"name": payload.name})

    @router.get("/missing")
    async def missing() -> None:
        raise HTTPException(status_code=404, detail="sensitive internal detail")

    @router.get("/unauthorized")
    async def unauthorized() -> None:
        raise HTTPException(
            status_code=401,
            detail="secret authentication detail",
            headers={"WWW-Authenticate": "Session"},
        )

    @router.get("/forbidden")
    async def forbidden() -> None:
        raise HTTPException(status_code=403, detail="secret authorization detail")

    @router.get("/expected")
    async def expected() -> None:
        raise ApiError(409, "VERSION_CONFLICT", "配置版本已变化")

    @router.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("secret-value-must-not-leak")

    @router.get("/base-exception")
    async def base_exception() -> None:
        raise CancellationProbe

    return router


@pytest.mark.parametrize(
    ("path", "method", "json", "status_code", "code", "message"),
    [
        (
            "/api/contract/validate",
            "POST",
            {"name": "accepted", "unknown": "secret-value-must-not-leak"},
            422,
            "VALIDATION_ERROR",
            "请求参数校验失败",
        ),
        ("/api/contract/missing", "GET", None, 404, "NOT_FOUND", "请求的资源不存在"),
        ("/api/contract/validate", "GET", None, 405, "METHOD_NOT_ALLOWED", "请求方法不受支持"),
        ("/api/contract/unauthorized", "GET", None, 401, "UNAUTHORIZED", "未认证"),
        ("/api/contract/forbidden", "GET", None, 403, "FORBIDDEN", "无权限访问"),
        (
            "/api/contract/expected",
            "GET",
            None,
            409,
            "VERSION_CONFLICT",
            "配置版本已变化",
        ),
        (
            "/api/contract/unexpected",
            "GET",
            None,
            500,
            "INTERNAL_SERVER_ERROR",
            "服务内部错误",
        ),
    ],
)
def test_errors_have_safe_stable_envelopes(
    path: str,
    method: str,
    json: object | None,
    status_code: int,
    code: str,
    message: str,
) -> None:
    app = create_app(
        Settings(environment="test"), AppComposition(routers=(_error_router(),))
    )

    response = request(
        app,
        method,
        path,
        json=json,
        raise_app_exceptions=True,
    )

    payload = response.json()
    assert response.status_code == status_code
    assert payload == {
        "code": code,
        "message": message,
        "data": None,
        "traceId": response.headers["x-trace-id"],
    }
    assert "secret-value-must-not-leak" not in response.text
    assert "sensitive internal detail" not in response.text
    assert "secret authentication detail" not in response.text
    assert "secret authorization detail" not in response.text
    if status_code == 401:
        assert response.headers["www-authenticate"] == "Session"


def test_unknown_route_uses_the_shared_404_envelope() -> None:
    response = request(create_app(Settings(environment="test")), "GET", "/api/not-present")

    assert response.status_code == 404
    assert response.json() == {
        "code": "NOT_FOUND",
        "message": "请求的资源不存在",
        "data": None,
        "traceId": response.headers["x-trace-id"],
    }


def test_catch_all_consumes_and_redacts_original_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("ERROR", logger="risk_platform.shared.errors")
    app = create_app(
        Settings(environment="test"), AppComposition(routers=(_error_router(),))
    )

    response = request(app, "GET", "/api/contract/unexpected", raise_app_exceptions=True)

    assert response.status_code == 500
    assert response.json()["traceId"] == response.headers["x-trace-id"]
    assert "secret-value-must-not-leak" not in response.text
    assert caplog.messages == [
        f"未处理的 HTTP 异常 traceId={response.headers['x-trace-id']}"
    ]
    assert "secret-value-must-not-leak" not in caplog.text


def test_catch_all_does_not_consume_base_exception_control_flow() -> None:
    app = create_app(
        Settings(environment="test"), AppComposition(routers=(_error_router(),))
    )

    with pytest.raises(CancellationProbe):
        request(app, "GET", "/api/contract/base-exception", raise_app_exceptions=True)


def test_cors_allows_only_configured_origin_with_credentials() -> None:
    app = create_app(Settings(environment="test", cors_origins=("https://web.internal",)))

    allowed = request(
        app,
        "OPTIONS",
        "/api/health",
        headers={
            "origin": "https://web.internal",
            "access-control-request-method": "GET",
        },
    )
    denied = request(
        app,
        "OPTIONS",
        "/api/health",
        headers={
            "origin": "https://evil.invalid",
            "access-control-request-method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://web.internal"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in denied.headers
    assert UUID(allowed.headers["x-trace-id"])


def test_cors_headers_and_trace_cover_a_redacted_500() -> None:
    app = create_app(
        Settings(environment="test", cors_origins=("https://web.internal",)),
        AppComposition(routers=(_error_router(),)),
    )

    response = request(
        app,
        "GET",
        "/api/contract/unexpected",
        headers={"origin": "https://web.internal"},
        raise_app_exceptions=True,
    )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "https://web.internal"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.json()["traceId"] == response.headers["x-trace-id"]
    assert "secret-value-must-not-leak" not in response.text


def _proxy_router() -> APIRouter:
    router = APIRouter(prefix="/proxy")

    @router.get("/context")
    async def context(request: Request) -> dict[str, str | None]:
        return {
            "client": request.client.host if request.client else None,
            "scheme": request.url.scheme,
        }

    return router


def test_forwarded_headers_require_an_explicitly_trusted_peer() -> None:
    headers = {"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https"}
    router = _proxy_router()
    untrusted_app = create_app(Settings(environment="test"), AppComposition(routers=(router,)))
    trusted_app = create_app(
        Settings(
            environment="test",
            trusted_proxy_cidrs=(ip_network("10.0.0.0/8"),),
        ),
        AppComposition(routers=(router,)),
    )

    untrusted = request(
        untrusted_app,
        "GET",
        "/api/proxy/context",
        headers=headers,
        client=("10.0.0.2", 50000),
    )
    trusted = request(
        trusted_app,
        "GET",
        "/api/proxy/context",
        headers=headers,
        client=("10.0.0.2", 50000),
    )

    assert untrusted.json() == {"client": "10.0.0.2", "scheme": "http"}
    assert trusted.json() == {"client": "198.51.100.7", "scheme": "https"}


def test_feature_router_dependency_override_and_lifespan_are_composable() -> None:
    events: list[str] = []
    app_reference: list[FastAPI] = []

    async def dependency() -> str:
        return "production"

    async def override() -> str:
        return "isolated-test"

    router = APIRouter(prefix="/feature")

    @router.get("/value")
    async def feature_value(value: str = Depends(dependency)) -> dict[str, str]:
        return {"value": value}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_reference.append(app)
        events.append("started")
        yield
        events.append("stopped")

    app = create_app(
        Settings(environment="test"),
        AppComposition(
            routers=(router,),
            dependency_overrides={dependency: override},
            lifespan=lifespan,
        ),
    )

    assert request(app, "GET", "/api/feature/value").json() == {"value": "isolated-test"}
    assert app_reference == [app]
    assert events == ["started", "stopped"]


def test_settings_are_validated_without_exposing_values() -> None:
    settings = Settings.from_env(
        {
            "NODE_ENV": "production",
            "API_PORT": "8443",
            "CORS_ORIGIN": "https://one.internal,https://two.internal",
            "TRUSTED_PROXY_CIDRS": "10.0.0.5/24,fd00::/64",
            "SESSION_SECRET_FILE": "/run/secrets/project_risk_session_key",
        }
    )

    assert settings.api_port == 8443
    assert settings.cors_origins == ("https://one.internal", "https://two.internal")
    assert settings.trusted_proxy_cidrs == (
        ip_network("10.0.0.0/24"),
        ip_network("fd00::/64"),
    )
    assert settings.session_cookie_secure is True

    with pytest.raises(SettingsError) as captured:
        Settings.from_env({"TRUSTED_PROXY_CIDRS": "secret-invalid-value"})
    assert "TRUSTED_PROXY_CIDRS" in str(captured.value)
    assert "secret-invalid-value" not in str(captured.value)


def test_ai_outbound_allowlists_are_normalized_and_validate_without_leaks() -> None:
    settings = Settings.from_env(
        {
            "AI_OUTBOUND_ALLOWED_HOSTNAMES": "TOKEN.LONGSHINE.COM.,ai.internal.example.com",
            "AI_OUTBOUND_ALLOWED_CIDRS": "10.0.0.1/8,172.16.1.1/12",
        }
    )

    assert settings.ai_outbound_allowed_hostnames == frozenset(
        {"token.longshine.com", "ai.internal.example.com"}
    )
    assert tuple(str(network) for network in settings.ai_outbound_allowed_cidrs) == (
        "10.0.0.0/8",
        "172.16.0.0/12",
    )
    for name, value in (
        ("AI_OUTBOUND_ALLOWED_HOSTNAMES", "bad hostname value"),
        ("AI_OUTBOUND_ALLOWED_HOSTNAMES", "*.internal.example.com"),
        ("AI_OUTBOUND_ALLOWED_HOSTNAMES", "https://ai.internal.example.com"),
        ("AI_OUTBOUND_ALLOWED_HOSTNAMES", "ai.internal.example.com:8443"),
        ("AI_OUTBOUND_ALLOWED_HOSTNAMES", "10.0.0.1"),
        ("AI_OUTBOUND_ALLOWED_CIDRS", "not-a-cidr"),
    ):
        with pytest.raises(SettingsError) as captured:
            Settings.from_env({name: value})
        assert value not in str(captured.value)


def test_cookie_security_defaults_match_environment() -> None:
    development = session_cookie_options(Settings(environment="development"))
    production = session_cookie_options(
        Settings(
            environment="production",
            session_secret_file=Path("/run/secrets/project_risk_session_key"),
        )
    )

    assert development == {
        "httponly": True,
        "secure": False,
        "samesite": "lax",
        "path": "/",
    }
    assert production == {**development, "secure": True}


def test_openapi_contains_liveness_and_envelope_schema() -> None:
    schema = create_app(Settings(environment="test")).openapi()

    operation = schema["paths"]["/api/health"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    component_name = response_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    envelope = schema["components"]["schemas"][component_name]

    assert set(envelope["required"]) == {"code", "message", "data", "traceId"}
    assert set(envelope["properties"]) == {"code", "message", "data", "traceId"}
