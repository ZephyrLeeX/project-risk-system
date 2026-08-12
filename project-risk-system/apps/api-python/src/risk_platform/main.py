"""ASGI entry point with the production dashboard query composition."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from risk_platform.app import AppComposition, create_app
from risk_platform.auth.service import AuthService
from risk_platform.dashboard.api import router as dashboard_router
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import (
    create_database_engine,
    create_session_factory,
    database_url,
    dispose_database_engine,
)
from risk_platform.risks.api import router as risks_router
from risk_platform.risks.service import RisksService


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create only process-owned database services; no schema is created at startup."""

    engine = create_database_engine(database_url())
    sessions = create_session_factory(engine)
    app.state.auth_service = AuthService.from_settings(sessions, app.state.settings)
    app.state.risks_service = RisksService(sessions)
    app.state.dashboard_service = DashboardService(sessions)
    try:
        yield
    finally:
        await dispose_database_engine(engine)


app = create_app(
    composition=AppComposition(
        routers=(dashboard_router, risks_router),
        lifespan=_lifespan,
    )
)

__all__ = ["app"]
