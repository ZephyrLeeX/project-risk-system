"""FastAPI application factory and module composition contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from risk_platform.config import Settings
from risk_platform.shared.errors import SafeExceptionMiddleware, install_exception_handlers
from risk_platform.shared.http import ApiResponse, HealthResponse, ok
from risk_platform.shared.security import TrustedProxyHeadersMiddleware
from risk_platform.shared.tracing import TraceMiddleware

type Dependency = Callable[..., Any]
type Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


@dataclass(frozen=True, slots=True)
class AppComposition:
    """Stable extension point for feature routers, tests and final T040 assembly.

    Feature routers own module-local prefixes and never repeat the global ``/api``
    prefix. Tests may provide dependency overrides at factory time. A composed
    lifespan owns only resources belonging to the supplied module set.
    """

    routers: tuple[APIRouter, ...] = ()
    dependency_overrides: Mapping[Dependency, Dependency] = field(default_factory=dict)
    lifespan: Lifespan | None = None


@asynccontextmanager
async def _empty_lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    yield


def _health_router() -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health", response_model=ApiResponse[HealthResponse])
    async def health(request: Request) -> ApiResponse[HealthResponse]:
        payload = HealthResponse(
            version=version("risk-platform-api"),
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        return ok(request, payload, "服务运行正常")

    return router


def create_app(
    settings: Settings | None = None,
    composition: AppComposition | None = None,
) -> FastAPI:
    """Create an isolated application without importing domain modules."""

    resolved_settings = settings or Settings.from_env()
    resolved_composition = composition or AppComposition()
    app = FastAPI(
        title="Project Risk Management API",
        version=version("risk-platform-api"),
        lifespan=resolved_composition.lifespan or _empty_lifespan,
    )
    app.state.settings = resolved_settings

    api_router = APIRouter(prefix="/api")
    api_router.include_router(_health_router())
    for router in resolved_composition.routers:
        api_router.include_router(router)
    app.include_router(api_router)
    app.dependency_overrides.update(resolved_composition.dependency_overrides)

    install_exception_handlers(app)
    app.add_middleware(
        TrustedProxyHeadersMiddleware,
        trusted_proxy_cidrs=resolved_settings.trusted_proxy_cidrs,
    )
    app.add_middleware(SafeExceptionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TraceMiddleware)
    return app


__all__ = ["AppComposition", "Dependency", "Lifespan", "create_app"]
