"""Shared FastAPI/Celery composition root (sole post-T002 owner: T040).

This module owns the production dependency graph shared by the HTTP
application and the Celery worker process.  Feature modules expose
module-local entry points (routers and ``handlers``/factory functions);
only this module constructs production dependencies, merges handler
mappings and registers the shared executor exactly once.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.options.service import AdminOptionsService
from risk_platform.admin.overview.service import AdminOverviewService
from risk_platform.admin.roles.service import AdminRolesService
from risk_platform.admin.users.service import AdminUsersService
from risk_platform.agent.execution import (
    MAX_RESPONSE_BYTES,
    AgentProviderError,
    Provider,
    ProviderTransportResponse,
    agent_execution_handlers,
)
from risk_platform.agent.models import AgentExecutionConfig
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.ai_providers.service import AiProvidersService
from risk_platform.audit.http import AuditQueryService
from risk_platform.auth.service import AuthService
from risk_platform.config import Settings
from risk_platform.dashboard.service import DashboardService
from risk_platform.imports import tasks as import_tasks
from risk_platform.imports.commit_service import ImportCommitService
from risk_platform.imports.service import ImportPreviewService
from risk_platform.imports.worker import ImportPreviewWorker
from risk_platform.mailbox import tasks as mailbox_tasks
from risk_platform.mailbox.extraction import MailRiskCandidateService, MailRiskExtractionWorker
from risk_platform.mailbox.parse_worker import MailParseWorker
from risk_platform.mailbox.service import MailboxService
from risk_platform.mailbox.sync import MailboxSyncService
from risk_platform.model_types import JSONValue
from risk_platform.reliability.core import TaskHandler
from risk_platform.retention import tasks as retention_tasks
from risk_platform.retention.cleanup import RetentionCleanupService
from risk_platform.retention.service import RetentionHoldService
from risk_platform.risks.service import RisksService
from risk_platform.shared.crypto import KeyRing, SecretCipher, SecretCryptoError
from risk_platform.shared.outbound import OutboundEndpointGuard, provider_subresource_url
from risk_platform.system_config.service import SystemConfigService
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports import tasks as weekly_tasks
from risk_platform.weekly_reports.service import WeeklyReportService

_ERROR_BODY_BYTES = 64 * 1024


class CompositionError(RuntimeError):
    """A required process secret or path is missing or invalid."""


def load_cipher(environ: Mapping[str, str] | None = None) -> SecretCipher | None:
    """Load the documented local encryption key without exposing its value."""

    source = os.environ if environ is None else environ
    encoded = source.get("DATA_ENCRYPTION_KEY")
    if not encoded:
        return None
    try:
        key = base64.b64decode(encoded, validate=True)
        return SecretCipher(KeyRing(active_version="v1", keys={"v1": key}))
    except (SecretCryptoError, ValueError):
        return None


def import_storage_root(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve the single import workbook storage root to an absolute path."""

    source = os.environ if environ is None else environ
    return Path(source.get("IMPORT_STORAGE_DIR", "storage/excel")).resolve()


class AgentProviderAdapter:
    """Production OpenAI-compatible transport for one Agent execution.

    The adapter is a ``Provider``: it receives an immutable
    ``AgentExecutionConfig`` snapshot and the validated request body, then
    returns only a raw ``ProviderTransportResponse``.  No request/response
    content or secret is persisted here; the worker performs all protocol
    validation.
    """

    def __init__(self, cipher: SecretCipher, guard: OutboundEndpointGuard | None = None) -> None:
        self._cipher = cipher
        self._guard = guard or OutboundEndpointGuard()

    async def __call__(
        self, config: AgentExecutionConfig, request: dict[str, JSONValue]
    ) -> ProviderTransportResponse:
        snapshot = config.encryptedApiKeySnapshot
        endpoint = config.endpointSnapshot
        model = config.modelSnapshot
        if snapshot is None or endpoint is None or model is None:
            raise AgentProviderError()
        api_key = self._cipher.decrypt(snapshot)
        try:
            resolved = await self._guard.resolve_provider(endpoint)
            url = provider_subresource_url(resolved, "chat/completions")
            await self._guard.revalidate(resolved)
            status, body = await asyncio.to_thread(
                self._request, url, api_key, model, request, config.timeoutSeconds
            )
        except TimeoutError:
            raise
        except (OSError, urllib.error.URLError):
            raise AgentProviderError() from None
        return ProviderTransportResponse(status, body)

    @staticmethod
    def _request(
        url: str,
        api_key: str,
        model: str,
        payload: dict[str, JSONValue],
        timeout_seconds: int,
    ) -> tuple[int, bytes]:
        body = json.dumps(
            {
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            },
            ensure_ascii=False,
        ).encode()
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                return int(response.status), response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            return error.code, error.read(_ERROR_BODY_BYTES)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, request: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def build_provider(cipher: SecretCipher) -> Provider:
    """Construct the production Agent Provider adapter from the secret boundary."""

    return AgentProviderAdapter(cipher)


def build_tool_registry(sessions: async_sessionmaker[AsyncSession]) -> AgentToolRegistry:
    """Assemble the closed T028 read-tool registry from existing domain services."""

    return AgentToolRegistry(
        DashboardService(sessions),
        RisksService(sessions),
        TodosService(sessions),
        WeeklyReportService(sessions),
    )


def build_services(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    cipher: SecretCipher | None,
    import_root: Path,
    *,
    overview_api_check: Callable[[], Awaitable[None]],
) -> dict[str, object]:
    """Construct every process-owned service bound by a module-local router.

    A missing or invalid encryption key fails composition explicitly because
    the AI-provider and mailbox services cannot be constructed without it.
    """

    if cipher is None:
        raise CompositionError("DATA_ENCRYPTION_KEY 未配置或无效")
    dashboard = DashboardService(sessions)
    risks = RisksService(sessions)
    todos = TodosService(sessions)
    weekly = WeeklyReportService(sessions)
    return {
        "auth_service": AuthService.from_settings(sessions, settings),
        "risks_service": risks,
        "todos_service": todos,
        "dashboard_service": dashboard,
        "weekly_report_service": weekly,
        "agent_conversation_service": AgentConversationService(sessions),
        "agent_tool_registry": AgentToolRegistry(dashboard, risks, todos, weekly),
        "retention_hold_service": RetentionHoldService(sessions),
        "admin_users_service": AdminUsersService(sessions),
        "admin_roles_service": AdminRolesService(sessions),
        "admin_options_service": AdminOptionsService(sessions),
        "ai_providers_service": AiProvidersService(sessions, cipher),
        "audit_query_service": AuditQueryService(sessions),
        "system_config_service": SystemConfigService(sessions),
        "mailbox_service": MailboxService(sessions, cipher),
        "mail_risk_candidate_service": MailRiskCandidateService(sessions),
        "import_preview_service": ImportPreviewService(sessions, import_root),
        "import_commit_service": ImportCommitService(sessions, import_root),
        "admin_overview_service": AdminOverviewService(
            sessions, cipher, api_check=overview_api_check
        ),
    }


def merge_worker_handlers(
    sessions: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
    import_root: Path,
    provider: Provider,
    tool_registry: AgentToolRegistry,
) -> Mapping[str, TaskHandler]:
    """Merge every module-local handler mapping into one closed registry."""

    merged: dict[str, TaskHandler] = {}
    merged.update(import_tasks.handlers(ImportPreviewWorker(sessions, str(import_root))))
    merged.update(
        mailbox_tasks.handlers(
            MailboxSyncService(sessions, cipher),
            MailParseWorker(sessions, cipher),
            MailRiskExtractionWorker(sessions, cipher),
        )
    )
    merged.update(weekly_tasks.handlers(WeeklyReportService(sessions)))
    merged.update(
        retention_tasks.handlers(
            RetentionCleanupService(
                sessions,
                import_storage_root=import_root,
                temp_storage_root=Path(tempfile.gettempdir()),
            )
        )
    )
    merged.update(agent_execution_handlers(sessions, provider, tool_registry))
    return merged


__all__ = [
    "AgentProviderAdapter",
    "CompositionError",
    "build_provider",
    "build_services",
    "build_tool_registry",
    "import_storage_root",
    "load_cipher",
    "merge_worker_handlers",
]
