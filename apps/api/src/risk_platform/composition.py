"""Shared FastAPI/Celery composition root (sole post-T002 owner: T040).

This module owns the production dependency graph shared by the HTTP
application and the Celery worker process.  Feature modules expose
module-local entry points (routers and ``handlers``/factory functions);
only this module constructs production dependencies, merges handler
mappings and registers the shared executor exactly once.
"""

from __future__ import annotations

import base64
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.options.service import AdminOptionsService
from risk_platform.admin.overview.service import AdminOverviewService
from risk_platform.admin.roles.service import AdminRolesService
from risk_platform.admin.users.service import AdminUsersService
from risk_platform.agent.core import ReadOnlyAgentCore
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.agent.v2_execution import native_agent_execution_handlers
from risk_platform.ai_providers.client import AiProviderClient
from risk_platform.ai_providers.service import AiProvidersService
from risk_platform.ai_providers.v2_adapter import (
    AiProviderAdapter,
    AiProviderAdapterRegistry,
    DeepSeekOfficialAdapter,
    ProviderType,
)
from risk_platform.ai_providers.v2_service import AiProviderV2Service, ProviderV2Runtime
from risk_platform.audit.http import AuditQueryService
from risk_platform.auth.service import AuthService
from risk_platform.auth.wechat import WechatUserInfoClient
from risk_platform.config import Settings
from risk_platform.dashboard.service import DashboardService
from risk_platform.imports import tasks as import_tasks
from risk_platform.imports.commit_service import ImportCommitService
from risk_platform.imports.service import ImportPreviewService
from risk_platform.imports.worker import ImportPreviewWorker
from risk_platform.mailbox import tasks as mailbox_tasks
from risk_platform.mailbox.extraction import MailRiskCandidateService, MailRiskExtractionWorker
from risk_platform.mailbox.parse_worker import MailParseWorker
from risk_platform.mailbox.resolution import MailProjectResolutionService
from risk_platform.mailbox.service import MailboxService
from risk_platform.mailbox.sync import MailboxSyncService
from risk_platform.mailbox.sync_results import MailSyncResultsService
from risk_platform.reliability.core import TaskHandler
from risk_platform.retention import tasks as retention_tasks
from risk_platform.retention.cleanup import RetentionCleanupService
from risk_platform.retention.service import RetentionHoldService
from risk_platform.risks.service import RisksService
from risk_platform.shared.crypto import KeyRing, SecretCipher, SecretCryptoError
from risk_platform.shared.outbound import (
    OutboundEndpointGuard,
    OutboundPolicy,
)
from risk_platform.system_config.service import SystemConfigService
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports import tasks as weekly_tasks
from risk_platform.weekly_reports.service import WeeklyReportService


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


def build_ai_outbound_policy(settings: Settings) -> OutboundPolicy:
    """Create the AI-only private-network exception policy.

    IMAP deliberately continues to use its default guard, so an AI endpoint
    allowlist cannot expand the mailbox connection boundary.
    """

    return OutboundPolicy(
        approved_internal_hostnames=settings.ai_outbound_allowed_hostnames,
        approved_internal_networks=settings.ai_outbound_allowed_cidrs,
    )


def build_ai_provider_client(settings: Settings) -> AiProviderClient:
    """Inject the validated AI policy through the provider client boundary."""

    return AiProviderClient(OutboundEndpointGuard(build_ai_outbound_policy(settings)))


def build_tool_registry(sessions: async_sessionmaker[AsyncSession]) -> AgentToolRegistry:
    """Assemble the closed T028 read-tool registry from existing domain services."""

    return AgentToolRegistry(
        sessions,
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
    provider_client = build_ai_provider_client(settings)
    provider_v2_registry = AiProviderAdapterRegistry(
        {ProviderType.DEEPSEEK_OFFICIAL: DeepSeekOfficialAdapter(cipher)}
    )
    return {
        "auth_service": AuthService.from_settings(sessions, settings),
        "wechat_user_info_client": (
            WechatUserInfoClient(
                settings.wechat_user_info_url,
                settings.wechat_user_info_timeout_seconds,
                settings.wechat_user_info_max_retries,
            )
            if settings.wechat_user_info_url is not None
            else None
        ),
        "risks_service": risks,
        "todos_service": todos,
        "dashboard_service": dashboard,
        "weekly_report_service": weekly,
        "agent_conversation_service": AgentConversationService(sessions),
        "agent_tool_registry": AgentToolRegistry(sessions, dashboard, risks, todos, weekly),
        "retention_hold_service": RetentionHoldService(sessions),
        "admin_users_service": AdminUsersService(sessions),
        "admin_roles_service": AdminRolesService(sessions),
        "admin_options_service": AdminOptionsService(sessions),
        "ai_providers_service": AiProvidersService(sessions, cipher, provider_client),
        "ai_provider_v2_service": AiProviderV2Service(
            sessions,
            cipher,
            provider_v2_registry.adapter_for(ProviderType.DEEPSEEK_OFFICIAL),
        ),
        "audit_query_service": AuditQueryService(sessions),
        "system_config_service": SystemConfigService(sessions),
        "mailbox_service": MailboxService(sessions, cipher),
        "mail_risk_candidate_service": MailRiskCandidateService(sessions),
        "mail_project_resolution_service": MailProjectResolutionService(sessions),
        "mail_sync_results_service": MailSyncResultsService(sessions),
        "import_preview_service": ImportPreviewService(sessions, import_root),
        "import_commit_service": ImportCommitService(sessions, import_root),
        "admin_overview_service": AdminOverviewService(
            sessions, cipher, provider_client, api_check=overview_api_check
        ),
    }


def merge_worker_handlers(
    sessions: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
    import_root: Path,
    tool_registry: AgentToolRegistry,
    ai_provider_client: AiProviderClient,
    *,
    agent_adapter: AiProviderAdapter | None = None,
) -> Mapping[str, TaskHandler]:
    """Merge every module-local handler mapping into one closed registry."""

    merged: dict[str, TaskHandler] = {}
    merged.update(import_tasks.handlers(ImportPreviewWorker(sessions, str(import_root))))
    # One guarded provider runtime is shared by the mailbox workers and the
    # agent core so a single transport instance serves the whole worker.
    # The optional adapter is an explicit composition seam for the E2E harness.
    # Production callers omit it and therefore always use the guarded official
    # DeepSeek transport; no environment value can select a test provider.
    worker_runtime = ProviderV2Runtime(sessions, agent_adapter or DeepSeekOfficialAdapter(cipher))
    merged.update(
        mailbox_tasks.handlers(
            MailboxSyncService(sessions, cipher),
            MailParseWorker(sessions, cipher, provider_runtime=worker_runtime),
            MailRiskExtractionWorker(sessions, cipher, provider_runtime=worker_runtime),
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
    merged.update(
        native_agent_execution_handlers(
            sessions,
            ReadOnlyAgentCore(
                worker_runtime,
                tool_registry,
            ),
        )
    )
    return merged


__all__ = [
    "CompositionError",
    "build_ai_outbound_policy",
    "build_ai_provider_client",
    "build_services",
    "build_tool_registry",
    "import_storage_root",
    "load_cipher",
    "merge_worker_handlers",
]
