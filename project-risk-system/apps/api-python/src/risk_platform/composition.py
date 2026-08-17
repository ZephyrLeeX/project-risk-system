"""Shared FastAPI/Celery composition root (sole post-T002 owner: T040).

This module owns the production dependency graph shared by the HTTP
application and the Celery worker process.  Feature modules expose
module-local entry points (routers and ``handlers``/factory functions);
only this module constructs production dependencies, merges handler
mappings and registers the shared executor exactly once.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.options.service import AdminOptionsService
from risk_platform.admin.overview.service import AdminOverviewService
from risk_platform.admin.roles.service import AdminRolesService
from risk_platform.admin.users.service import AdminUsersService
from risk_platform.agent.core import ReadOnlyAgentCore
from risk_platform.agent.execution import AgentProviderError, Provider, ProviderTransportResponse
from risk_platform.agent.models import AgentExecutionConfig
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.agent.v2_execution import native_agent_execution_handlers
from risk_platform.ai_providers.client import (
    AGENT_RESPONSE_TRANSPORT_RETRY_COUNT,
    AiProviderClient,
    ProviderRequestError,
)
from risk_platform.ai_providers.models import AiProviderProtocol
from risk_platform.ai_providers.service import AiProvidersService
from risk_platform.ai_providers.v2_adapter import (
    AiProviderAdapterRegistry,
    DeepSeekOfficialAdapter,
    ProviderType,
)
from risk_platform.ai_providers.v2_service import AiProviderV2Service, ProviderV2Runtime
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
from risk_platform.mailbox.sync_results import MailSyncResultsService
from risk_platform.model_types import JSONValue
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
        self._client = AiProviderClient(guard or OutboundEndpointGuard())

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
            protocol = AiProviderProtocol(config.protocolSnapshot or "OPENAI_CHAT_COMPLETIONS")
            if protocol is AiProviderProtocol.OPENAI_RESPONSES:
                return await self._responses(config, request, api_key)
            result = await self._client.complete(
                endpoint, protocol, model, api_key, request, config.timeoutSeconds, 0
            )
        except ProviderRequestError as error:
            if error.code == "PROVIDER_INVALID_OUTPUT":
                raise AgentProviderError(
                    code="AGENT_PROVIDER_INVALID_OUTPUT", retryable=False
                ) from None
            if error.status_code is not None and 400 <= error.status_code < 500:
                raise AgentProviderError(
                    code="AGENT_PROVIDER_REQUEST_REJECTED", retryable=False
                ) from None
            status = {
                "UPSTREAM_TIMEOUT": 408,
                "AUTHENTICATION_FAILED": 401,
                "MODEL_NOT_FOUND": 404,
                "INVALID_REQUEST": 400,
            }.get(error.code)
            raise AgentProviderError(status_code=status) from None
        except (TimeoutError, ValueError):
            raise AgentProviderError() from None
        return ProviderTransportResponse(200, result.text.encode())

    async def _responses(
        self,
        config: AgentExecutionConfig,
        request: dict[str, JSONValue],
        api_key: str,
    ) -> ProviderTransportResponse:
        """Normalize Responses items; never require transport JSON to be Agent V2."""
        phase_value = request.get("phase")
        if not isinstance(phase_value, str) or phase_value not in {"PLAN", "RESPOND"}:
            raise AgentProviderError(code="AGENT_PROVIDER_INVALID_OUTPUT", retryable=False)
        phase = phase_value
        native_request = dict(request)
        native_request["_responsesNativeTools"] = cast(JSONValue, self._native_tools(request))
        try:
            result = await self._client.complete_response(
                config.endpointSnapshot or "",
                config.modelSnapshot or "",
                api_key,
                native_request,
                config.timeoutSeconds,
                phase=phase,
            )
            normalized = self._normalize_responses(result.value, phase)
            return ProviderTransportResponse(
                200, json.dumps(normalized, ensure_ascii=False).encode()
            )
        except ProviderRequestError as error:
            # Native fallback is permitted only after the transport positively
            # classified this request's tool capability as unsupported.  A generic
            # 5xx stays retryable upstream failure and cannot change protocols.
            if error.code == "NATIVE_TOOLS_UNSUPPORTED":
                fallback = dict(request)
                fallback["systemInstruction"] = (
                    str(request.get("systemInstruction", ""))
                    + "\nCompatibility mode: output one strict JSON object conforming to "
                    + "AGENT_PROVIDER_EXECUTION_V2; no Markdown or code fences."
                )
                text = await self._client.complete(
                    config.endpointSnapshot or "",
                    AiProviderProtocol.OPENAI_RESPONSES,
                    config.modelSnapshot or "",
                    api_key,
                    fallback,
                    config.timeoutSeconds,
                    AGENT_RESPONSE_TRANSPORT_RETRY_COUNT,
                    phase=phase,
                    backoff=True,
                )
                logging.getLogger(__name__).info(
                    "agent provider capability phase=%s native_tools=unsupported "
                    "fallback=text_json upstream_status=%s",
                    phase,
                    error.status_code,
                )
                return ProviderTransportResponse(200, text.text.encode())
            raise

    @staticmethod
    def _native_tools(request: dict[str, JSONValue]) -> list[dict[str, object]]:
        values = request.get("tools", [])
        if not isinstance(values, list):
            return []
        return [
            {
                "type": "function",
                "name": item["name"],
                "description": item["description"],
                "parameters": item["argumentsSchema"],
            }
            for item in values
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("description"), str)
            and isinstance(item.get("argumentsSchema"), dict)
        ]

    @classmethod
    def _normalize_responses(cls, value: dict[str, object], phase: str) -> dict[str, object]:
        output = value.get("output")
        if not isinstance(output, list):
            cls._diagnostic(phase, "EMPTY_PROVIDER_OUTPUT", ())
            raise ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False)
        item_types = tuple(str(item.get("type")) for item in output if isinstance(item, dict))
        calls = [
            item
            for item in output
            if isinstance(item, dict) and item.get("type") == "function_call"
        ]
        text = "".join(
            str(block["text"])
            for item in output
            if isinstance(item, dict) and item.get("type") == "message"
            for block in item.get("content", [])
            if isinstance(block, dict)
            and block.get("type") == "output_text"
            and isinstance(block.get("text"), str)
        )
        if phase == "PLAN" and calls:
            actions: list[dict[str, object]] = []
            for call in calls:
                name, arguments = call.get("name"), call.get("arguments")
                try:
                    decoded = json.loads(arguments) if isinstance(arguments, str) else None
                except json.JSONDecodeError:
                    decoded = None
                if not isinstance(name, str) or not isinstance(decoded, dict):
                    cls._diagnostic(phase, "SCHEMA_VALIDATION_FAILED", item_types)
                    raise ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False)
                actions.append({"type": "tool_call", "name": name, "arguments": decoded})
            cls._diagnostic(phase, "native_function_call", item_types)
            logging.getLogger(__name__).info(
                "agent provider plan tools=%s",
                ",".join(str(action["name"]) for action in actions),
            )
            return {
                "protocol": "AGENT_PROVIDER_EXECUTION_V2",
                "phase": phase,
                "grounded": True,
                "actions": actions,
            }
        if phase == "RESPOND" and text:
            cls._diagnostic(phase, "message_text", item_types)
            return {
                "protocol": "AGENT_PROVIDER_EXECUTION_V2",
                "phase": phase,
                "grounded": True,
                "actions": [{"type": "text_delta", "text": text}],
            }
        outcome = (
            "NO_TOOL_ACTION"
            if phase == "PLAN" and not calls
            else "NO_TEXT_ACTION"
            if phase == "RESPOND" and not text
            else "UNSUPPORTED_PROVIDER_RESPONSE_ITEM"
        )
        cls._diagnostic(phase, outcome, item_types)
        raise ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False)

    @staticmethod
    def _diagnostic(phase: str, outcome: str, item_types: tuple[str, ...]) -> None:
        logging.getLogger(__name__).info(
            "agent provider normalized phase=%s transport=OPENAI_RESPONSES "
            "outcome=%s item_types=%s",
            phase,
            outcome,
            ",".join(item_types) or "none",
        )


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


def build_provider(cipher: SecretCipher, settings: Settings) -> Provider:
    """Construct the production Agent Provider adapter from the secret boundary."""

    return AgentProviderAdapter(cipher, OutboundEndpointGuard(build_ai_outbound_policy(settings)))


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
    provider: Provider,
    tool_registry: AgentToolRegistry,
    ai_provider_client: AiProviderClient,
) -> Mapping[str, TaskHandler]:
    """Merge every module-local handler mapping into one closed registry."""

    merged: dict[str, TaskHandler] = {}
    merged.update(import_tasks.handlers(ImportPreviewWorker(sessions, str(import_root))))
    merged.update(
        mailbox_tasks.handlers(
            MailboxSyncService(sessions, cipher),
            MailParseWorker(sessions, cipher),
            MailRiskExtractionWorker(sessions, cipher, ai_provider_client),
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
    del provider  # V2 Agent execution exclusively uses the ADR 0034 runtime.
    runtime = ProviderV2Runtime(sessions, DeepSeekOfficialAdapter(cipher))
    merged.update(
        native_agent_execution_handlers(sessions, ReadOnlyAgentCore(runtime, tool_registry))
    )
    return merged


__all__ = [
    "AgentProviderAdapter",
    "CompositionError",
    "build_ai_outbound_policy",
    "build_ai_provider_client",
    "build_provider",
    "build_services",
    "build_tool_registry",
    "import_storage_root",
    "load_cipher",
    "merge_worker_handlers",
]
