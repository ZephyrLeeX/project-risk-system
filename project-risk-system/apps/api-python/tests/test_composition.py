"""T040 composition tests: service graph, route uniqueness, worker handler merge."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.ai_providers.service import AiProvidersService
from risk_platform.auth.service import AuthService
from risk_platform.composition import (
    build_ai_provider_client,
    build_provider,
    build_services,
    build_tool_registry,
    merge_worker_handlers,
)
from risk_platform.config import Settings
from risk_platform.dashboard.service import DashboardService
from risk_platform.db import create_database_engine, create_session_factory
from risk_platform.mailbox.extraction import MailRiskCandidateService
from risk_platform.mailbox.service import MailboxService
from risk_platform.mailbox.sync_results import MailSyncResultsService
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.retention.service import RetentionHoldService
from risk_platform.risks.service import RisksService
from risk_platform.shared.crypto import KeyRing, SecretCipher

EXPECTED_SERVICE_NAMES = frozenset(
    {
        "auth_service",
        "risks_service",
        "todos_service",
        "dashboard_service",
        "weekly_report_service",
        "agent_conversation_service",
        "agent_tool_registry",
        "retention_hold_service",
        "admin_users_service",
        "admin_roles_service",
        "admin_options_service",
        "ai_providers_service",
        "ai_provider_v2_service",
        "audit_query_service",
        "system_config_service",
        "mailbox_service",
        "mail_risk_candidate_service",
        "mail_sync_results_service",
        "import_preview_service",
        "import_commit_service",
        "admin_overview_service",
    }
)

REPRESENTATIVE_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/login",
        "/api/admin/users",
        "/api/admin/roles",
        "/api/admin/departments",
        "/api/admin/overview",
        "/api/admin/ai-services",
        "/api/admin/audit-logs",
        "/api/admin/system-config",
        "/api/admin/retention-holds",
        "/api/dashboard/summary",
        "/api/risks",
        "/api/todos",
        "/api/weekly-reports/current",
        "/api/imports/project-list/preview",
        "/api/mailbox/me",
        "/api/mailbox/risk-candidates/{candidate_id}",
        "/api/agent/help",
    }
)


def _sessions() -> async_sessionmaker[AsyncSession]:
    return create_session_factory(
        create_database_engine("postgresql+psycopg://user:pass@localhost:5432/db")
    )


def _cipher() -> SecretCipher:
    return SecretCipher(KeyRing(active_version="v1", keys={"v1": b"1" * 32}))


def _settings(tmp_path: Path) -> Settings:
    secret = tmp_path / "session.key"
    secret.write_bytes(b"s" * 32)
    return Settings(environment="test", session_secret_file=secret)


async def _noop_check() -> None:
    return None


def _flatten(routes: list[Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for route in routes:
        if hasattr(route, "methods"):
            result.extend((route.path, method) for method in route.methods)
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            result.extend(_flatten(list(nested.routes)))
            continue
        subroutes = getattr(route, "routes", None)
        if subroutes is not None:
            result.extend(_flatten(list(subroutes)))
    return result


def test_worker_handlers_cover_every_task_kind(tmp_path: Path) -> None:
    sessions = _sessions()
    cipher = _cipher()
    handlers = merge_worker_handlers(
        sessions,
        cipher,
        tmp_path,
        build_provider(cipher, _settings(tmp_path)),
        build_tool_registry(sessions),
        build_ai_provider_client(_settings(tmp_path)),
    )

    assert set(handlers) == {kind.value for kind in DurableTaskKind}
    assert len(handlers) == len(DurableTaskKind)


def test_build_services_provides_every_router_dependency(tmp_path: Path) -> None:
    sessions = _sessions()
    services = build_services(
        sessions,
        _settings(tmp_path),
        _cipher(),
        tmp_path,
        overview_api_check=_noop_check,
    )

    assert set(services) == EXPECTED_SERVICE_NAMES
    assert isinstance(services["auth_service"], AuthService)
    assert isinstance(services["risks_service"], RisksService)
    assert isinstance(services["dashboard_service"], DashboardService)
    assert isinstance(services["mailbox_service"], MailboxService)
    assert isinstance(services["mail_risk_candidate_service"], MailRiskCandidateService)
    assert isinstance(services["mail_sync_results_service"], MailSyncResultsService)
    assert isinstance(services["ai_providers_service"], AiProvidersService)
    assert isinstance(services["agent_conversation_service"], AgentConversationService)
    assert isinstance(services["agent_tool_registry"], AgentToolRegistry)
    assert isinstance(services["retention_hold_service"], RetentionHoldService)


def test_full_app_registers_every_route_exactly_once() -> None:
    from risk_platform.main import app

    operations = _flatten(list(app.routes))
    assert len(operations) == len(set(operations)), "a (path, method) pair appears more than once"

    paths = set(app.openapi()["paths"])
    missing = REPRESENTATIVE_PATHS - paths
    assert not missing, f"missing representative routes: {missing}"


def test_full_app_lifespan_boots_and_shuts_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "session.key"
    secret.write_bytes(b"s" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", base64.b64encode(b"1" * 32).decode())
    monkeypatch.setenv("IMPORT_STORAGE_DIR", str(tmp_path / "excel"))
    monkeypatch.setenv("SESSION_SECRET_FILE", str(secret))

    from risk_platform.main import _lifespan, app

    app.state.settings = Settings.from_env()

    async def scenario() -> None:
        async with _lifespan(app):
            for name in EXPECTED_SERVICE_NAMES:
                assert getattr(app.state, name, None) is not None, name

    asyncio.run(scenario())


def test_register_production_worker_registers_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", base64.b64encode(b"1" * 32).decode())
    monkeypatch.setenv("IMPORT_STORAGE_DIR", str(tmp_path))

    import risk_platform.worker as worker

    worker._registered = False
    calls: list[object] = []
    monkeypatch.setattr(worker, "register_executor", lambda *a, **k: calls.append(a))

    worker.register_production_worker()
    worker.register_production_worker()

    assert len(calls) == 1
