"""ASGI entry point with the full production composition."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from risk_platform.admin.options.api import router as admin_options_router
from risk_platform.admin.overview.api import router as overview_router
from risk_platform.admin.overview.service import OverviewDependencyFailure
from risk_platform.admin.roles.api import router as admin_roles_router
from risk_platform.admin.users.api import router as admin_users_router
from risk_platform.agent.api import router as agent_router
from risk_platform.ai_providers.api import router as ai_providers_router
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.api import router as audit_router
from risk_platform.auth.api import router as auth_router
from risk_platform.composition import build_services, import_storage_root, load_cipher
from risk_platform.dashboard.api import router as dashboard_router
from risk_platform.db import (
    create_database_engine,
    create_session_factory,
    database_url,
    dispose_database_engine,
)
from risk_platform.imports.api import router as imports_router
from risk_platform.mailbox.api import candidate_router
from risk_platform.mailbox.api import router as mailbox_router
from risk_platform.mailbox.sync_results import router as mailbox_sync_results_router
from risk_platform.retention.api import router as retention_router
from risk_platform.risks.api import router as risks_router
from risk_platform.system_config.api import router as system_config_router
from risk_platform.todos.api import router as todos_router
from risk_platform.weekly_reports.api import router as weekly_reports_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create only process-owned database services; no schema is created at startup."""

    engine = create_database_engine(database_url())
    sessions = create_session_factory(engine)

    async def api_check() -> None:
        if not _overview_route_registered(app):
            raise OverviewDependencyFailure("CHECK_FAILED")

    services = build_services(
        sessions,
        app.state.settings,
        load_cipher(),
        import_storage_root(),
        overview_api_check=api_check,
    )
    for name, service in services.items():
        setattr(app.state, name, service)
    try:
        yield
    finally:
        await dispose_database_engine(engine)


app = create_app(
    composition=AppComposition(
        routers=(
            auth_router,
            dashboard_router,
            risks_router,
            todos_router,
            weekly_reports_router,
            overview_router,
            admin_users_router,
            admin_roles_router,
            admin_options_router,
            ai_providers_router,
            audit_router,
            system_config_router,
            retention_router,
            mailbox_router,
            candidate_router,
            mailbox_sync_results_router,
            imports_router,
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
