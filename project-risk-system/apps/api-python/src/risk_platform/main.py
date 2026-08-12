"""ASGI entry point with the production dashboard query composition."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from risk_platform.admin.overview.api import router as overview_router
from risk_platform.admin.overview.service import AdminOverviewService, OverviewDependencyFailure
from risk_platform.agent.api import router as agent_router
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
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
from risk_platform.retention.api import router as retention_router
from risk_platform.retention.service import RetentionHoldService
from risk_platform.risks.api import router as risks_router
from risk_platform.risks.service import RisksService
from risk_platform.shared.crypto import KeyRing, SecretCipher, SecretCryptoError
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import WeeklyReportService


def _overview_cipher() -> SecretCipher | None:
    """Load the documented local encryption key without exposing its value."""

    encoded = os.environ.get("DATA_ENCRYPTION_KEY")
    if not encoded:
        return None
    try:
        key = base64.b64decode(encoded, validate=True)
        return SecretCipher(KeyRing(active_version="v1", keys={"v1": key}))
    except (SecretCryptoError, ValueError):
        return None


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create only process-owned database services; no schema is created at startup."""

    engine = create_database_engine(database_url())
    sessions = create_session_factory(engine)
    app.state.auth_service = AuthService.from_settings(sessions, app.state.settings)
    app.state.risks_service = RisksService(sessions)
    app.state.todos_service = TodosService(sessions)
    app.state.dashboard_service = DashboardService(sessions)
    weekly_reports = WeeklyReportService(sessions)
    app.state.weekly_report_service = weekly_reports
    app.state.agent_conversation_service = AgentConversationService(sessions)
    app.state.agent_tool_registry = AgentToolRegistry(
        app.state.dashboard_service,
        app.state.risks_service,
        app.state.todos_service,
        weekly_reports,
    )
    app.state.retention_hold_service = RetentionHoldService(sessions)

    async def api_check() -> None:
        if (
            getattr(app.state, "auth_service", None) is None
            or getattr(app.state, "admin_overview_service", None) is None
            or not _overview_route_registered(app)
        ):
            raise OverviewDependencyFailure("CHECK_FAILED")

    app.state.admin_overview_service = AdminOverviewService(
        sessions, _overview_cipher(), api_check=api_check
    )
    try:
        yield
    finally:
        await dispose_database_engine(engine)


app = create_app(
    composition=AppComposition(
        routers=(
            dashboard_router,
            risks_router,
            overview_router,
            retention_router,
            agent_router,
        ),
        lifespan=_lifespan,
    )
)


def _overview_route_registered(application: FastAPI) -> bool:
    def contains(items: Sequence[object]) -> bool:
        for item in items:
            if getattr(item, "path", None) == "/admin/overview":
                return True
            nested = getattr(getattr(item, "original_router", None), "routes", ())
            if contains(nested):
                return True
        return False

    return contains(application.router.routes)


__all__ = ["app"]
