"""Provider-neutral V2 contract and the DeepSeek Official transport adapter."""

from __future__ import annotations

import asyncio
import http.client
import json
import math
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from risk_platform.model_types import JSONValue
from risk_platform.shared.crypto import SecretCipher, SecretCryptoError
from risk_platform.shared.outbound import (
    OutboundEndpointGuard,
    OutboundSecurityError,
    ResolvedEndpoint,
    provider_subresource_url,
)

DEEPSEEK_OFFICIAL_ORIGIN = "https://api.deepseek.com"
MAX_RETRY_AFTER_SECONDS = 10.0

# HTTP transport byte-safety caps, independent of model token capacity.  These
# are *wire* safety limits — how many bytes the adapter will read from or send
# to the provider socket — not model context capacity (tokens) and not the
# Agent product's context policy.  A request that already fits the token budget
# (``ContextBudget``, ~616k input tokens for DeepSeek V4) is well below these
# caps in bytes, so the caps never bind on legitimate large-context requests;
# they exist only as an independent fail-closed net against a runaway or
# malformed peer and an accidental memory blow-up.  8 MiB comfortably carries a
# full DeepSeek V4 1M-context request (worst-case ~2 MiB of CJK at ~3 B/token
# plus JSON framing) and a full output (max_tokens 384k -> ~1.5 MiB) including
# thinking ``reasoning_content``, while still bounding per-request memory.
DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# Backward-compatible module aliases for the default transport policy values,
# kept so tests and any external reference can express "the default safety cap"
# by name.  The real, configurable limits live on ``ProviderTransportPolicy`` and
# are threaded through the adapter/transport, independent of token budgets.
MAX_REQUEST_BYTES = DEFAULT_MAX_REQUEST_BYTES
MAX_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES
# The model name configured for a provider candidate is bounded at 128
# *characters* (``v2_schemas.CreateModelConfigRequest.modelName``:
# ``max_length=128``) — a character limit, not a UTF-8/wire-byte limit, so 128
# CJK chars (384 B), 128 emoji (512 B) or 128 control chars are all legal.  The
# budget serializer sizes the adapter's ``model`` wire field with a placeholder
# whose JSON-encoded form is >= any legal 128-char name: under
# ``json.dumps(..., ensure_ascii=False)`` the most expensive character is a
# JSON control char (U+0000-U+001F), always escaped to ``\uXXXX`` (6 bytes),
# which dominates emoji (4 B) and CJK (3 B).  128 control chars -> 768 bytes for
# the field value (770 with quotes), >= any legal 128-char name on the wire.
MAX_MODEL_NAME_CHARS = 128
_WORST_CASE_MODEL_NAME = "\x00" * MAX_MODEL_NAME_CHARS


class ProviderType(StrEnum):
    DEEPSEEK_OFFICIAL = "DEEPSEEK_OFFICIAL"


class ProviderRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ProviderFinishReason(StrEnum):
    STOP = "STOP"
    TOOL_CALLS = "TOOL_CALLS"
    LENGTH = "LENGTH"
    CONTENT_FILTER = "CONTENT_FILTER"
    OTHER = "OTHER"


class ProviderResponseFormat(StrEnum):
    """Provider-neutral response-shaping capability."""

    JSON_OBJECT = "JSON_OBJECT"


class ProviderErrorClassification(StrEnum):
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSIENT_SERVER = "TRANSIENT_SERVER"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    AUTHENTICATION = "AUTHENTICATION"
    PERMISSION = "PERMISSION"
    INVALID_REQUEST = "INVALID_REQUEST"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    PROTOCOL = "PROTOCOL"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"


class ProviderError(RuntimeError):
    """Typed, content-free Provider failure safe to expose across the adapter boundary."""

    def __init__(
        self,
        classification: ProviderErrorClassification,
        *,
        retryable: bool,
        failover_allowed: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(classification.value)
        self.classification: ProviderErrorClassification = classification
        self.retryable: bool = retryable
        self.failover_allowed: bool = failover_allowed
        self.status_code: int | None = status_code
        self.retry_after_seconds: float | None = retry_after_seconds


class ProviderCandidatesExhausted(ProviderError):
    def __init__(self, last_error: ProviderError) -> None:
        super().__init__(
            last_error.classification,
            retryable=last_error.retryable,
            failover_allowed=False,
            status_code=last_error.status_code,
        )


@dataclass(frozen=True, slots=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    role: ProviderRole
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ProviderToolCall, ...] = ()
    # DeepSeek thinking-mode reasoning, round-tripped only within one
    # execution's multi-round tool-call sequence (see ``_canonical_chat_payload``).
    # It is never surfaced to the user UI and never persisted as conversation
    # memory — it is an internal assistant-channel field the provider requires to
    # be echoed back on the next round when the assistant message that produced it
    # is replayed, and historical turns loaded from storage carry no reasoning.
    reasoning_content: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class ProviderChatRequest:
    messages: tuple[ProviderMessage, ...]
    tools: tuple[ProviderToolDefinition, ...] = ()
    response_format: ProviderResponseFormat | None = None


def _canonical_chat_payload(
    request: ProviderChatRequest, model_name: str
) -> dict[str, object]:
    """The single canonical wire serialization shared by the adapter and the
    provider-neutral request budget.

    Both the DeepSeek adapter (``_chat_payload``) and the provider-neutral
    ``measure_provider_request`` / ``measure_provider_request_tokens`` serialize
    through this function so the two can never silently drift apart.  The
    structure mirrors exactly what the adapter puts on the wire: the ``model``
    field, every message role/content/``tool_call_id``, assistant ``tool_calls``
    wrapped in ``type``/``function`` with ``arguments`` JSON-stringified (so
    quote/backslash escaping expansion is counted), assistant
    ``reasoning_content`` (DeepSeek thinking-mode reasoning, echoed back on the
    next round when the producing assistant message is replayed),
    ``response_format`` and every tool definition wrapped in
    ``type``/``function`` with ``parameters``.  ``measure_provider_request``
    calls this with a worst-case ``model_name`` so its byte count is always >=
    the real adapter-encoded payload size.
    """

    if not request.messages:
        raise ProviderError(
            ProviderErrorClassification.INVALID_REQUEST,
            retryable=False,
            failover_allowed=False,
        )
    messages: list[dict[str, object]] = []
    for message in request.messages:
        item: dict[str, object] = {"role": message.role.value}
        if message.content is not None:
            item["content"] = message.content
        if message.tool_call_id is not None:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            dict(call.arguments), ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                }
                for call in message.tool_calls
            ]
        if message.reasoning_content is not None:
            item["reasoning_content"] = message.reasoning_content
        messages.append(item)
    payload: dict[str, object] = {"model": model_name, "messages": messages}
    if request.response_format is ProviderResponseFormat.JSON_OBJECT:
        payload["response_format"] = {"type": "json_object"}
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                },
            }
            for tool in request.tools
        ]
    return payload


def _encode_chat_payload(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode()


def measure_provider_request(request: ProviderChatRequest) -> int:
    """Provider-neutral serialized-byte budget for a full chat request.

    Unlike a tokenizer, this is a stable, conservative byte count.  It shares
    the canonical wire serializer with the DeepSeek adapter and sizes the
    ``model`` field with the worst-case ``_WORST_CASE_MODEL_NAME`` placeholder
    (128 JSON control chars -> 768 wire bytes), so the result is always >= the
    real adapter-encoded payload size for any legal 128-character candidate
    model name, including multibyte CJK/emoji and quote/backslash/control-heavy
    names.  This lets the Agent core fail closed *before* any HTTP call, without
    depending on DeepSeek's tokenizer, wire framing, or any DeepSeek-specific
    field — only the provider-neutral ``ProviderChatRequest``.
    """

    payload = _canonical_chat_payload(request, _WORST_CASE_MODEL_NAME)
    return len(_encode_chat_payload(payload))


def measure_provider_request_tokens(
    request: ProviderChatRequest, estimator: TokenEstimator
) -> int:
    """Provider-neutral token estimate of the *full* chat request sent to model.

    Shares the canonical wire serializer (``_canonical_chat_payload`` +
    ``_encode_chat_payload``) with the DeepSeek adapter so the measurement can
    never drift from what is actually put on the wire.  The serialized payload
    covers everything the model sees: the ``model`` field, every message
    role/content/``tool_call_id``, assistant ``tool_calls`` (id, ``type``/
    ``function`` wrapper, function name and JSON-stringified ``arguments``),
    assistant ``reasoning_content`` (thinking-mode round-trip),
    ``response_format`` and every tool definition (``type``/``function`` wrapper,
    name, description, ``parameters``) — including all JSON framing bytes.

    ``estimator`` sizes that serialized payload in tokens (conservative: never
    under-counts), so the Agent core can fail closed on the *effective* model
    input budget before any HTTP call.  This is model-context capacity (tokens),
    distinct from ``measure_provider_request`` (wire bytes, transport safety)
    and from ``ProviderTransportPolicy`` (HTTP transport byte caps).  The
    ``model`` field is sized with the worst-case ``_WORST_CASE_MODEL_NAME`` so
    the estimate is always >= the real adapter-encoded request for any legal
    128-character model name.
    """

    payload = _canonical_chat_payload(request, _WORST_CASE_MODEL_NAME)
    return estimator.estimate(_encode_chat_payload(payload).decode("utf-8"))


@dataclass(frozen=True, slots=True)
class ProviderTokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class ProviderChatResponse:
    content: str | None
    tool_calls: tuple[ProviderToolCall, ...]
    finish_reason: ProviderFinishReason
    usage: ProviderTokenUsage
    latency_ms: int
    # DeepSeek thinking-mode reasoning produced with this response.  Round-tripped
    # onto the assistant message Core appends for the next round; never reaches
    # ``AgentCoreOutcome`` (the user-visible text) or persisted memory.
    reasoning_content: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderModelInfo:
    id: str


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Provider-neutral model context capacity (tokens), owned by the adapter.

    This is *model* capacity — how much context the model itself can hold and
    emit — not the Agent product's context policy, and not the HTTP transport
    byte safety limit (``MAX_REQUEST_BYTES``).  Agent Core consumes an
    *effective* capability snapshot derived from the failover candidates and
    must never hardcode a DeepSeek value here.
    """

    context_window_tokens: int
    max_output_tokens: int


def _deepseek_official_capabilities(model_name: str) -> ModelCapabilities:
    """DeepSeek Official model context per the official current model ability.

    DeepSeek's chat models expose a 1,000,000-token context window with up to
    384,000 output tokens (max_tokens).  All current DeepSeek Official chat
    models share this envelope, so the capability is keyed off the provider
    type rather than a per-model catalog entry.  If a future model diverges,
    branch on ``model_name`` here — the Agent Core side stays provider-neutral.
    """

    del model_name
    return ModelCapabilities(
        context_window_tokens=1_000_000,
        max_output_tokens=384_000,
    )


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    account_id: UUID
    account_name: str
    provider_type: ProviderType
    model_config_id: UUID
    model_name: str
    timeout_seconds: int
    encrypted_api_key: str
    capabilities: ModelCapabilities | None = None

    def effective_capabilities(self) -> ModelCapabilities:
        if self.capabilities is None:
            raise ProviderError(
                ProviderErrorClassification.INVALID_REQUEST,
                retryable=False,
                failover_allowed=False,
            )
        return self.capabilities


def effective_candidate_capabilities(
    candidates: tuple[ProviderCandidate, ...],
) -> ModelCapabilities:
    """The effective model capacity across a failover chain.

    Failover may route a request to any candidate in the chain, so the
    effective context window is the *minimum* window across candidates (a
    larger-window candidate cannot rescue a request shaped for a smaller one)
    and the effective output limit is the minimum output cap.  This guarantees
    a request that fits the effective budget cannot overflow any failover
    candidate.  Raises when any candidate lacks capabilities (fail closed).
    """

    if not candidates:
        raise ProviderError(
            ProviderErrorClassification.CREDENTIAL_UNAVAILABLE,
            retryable=False,
            failover_allowed=False,
        )
    windows = [candidate.effective_capabilities().context_window_tokens for candidate in candidates]
    outputs = [
        candidate.effective_capabilities().max_output_tokens for candidate in candidates
    ]
    return ModelCapabilities(
        context_window_tokens=min(windows),
        max_output_tokens=min(outputs),
    )


class TokenEstimator(Protocol):
    """Provider-neutral, conservative token-count estimate for a text string.

    This is the *only* token notion the Agent core consumes for its context
    budget.  It is deliberately an over-estimate (never under), so a request
    that fits the estimated budget also fits the real model budget.  It is
    distinct from the HTTP transport byte safety limit
    (``MAX_REQUEST_BYTES`` / ``measure_provider_request``): the estimator
    sizes *model context capacity* in tokens, the byte guard sizes *the wire
    payload* in bytes, and both must hold independently.
    """

    def estimate(self, text: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ByteTokenEstimator:
    """Baseline estimator: one estimated token per UTF-8 byte.

    Tokens never exceed bytes for any text a tokenizer could emit (a BPE byte
    is one token, a multi-byte char is one or more tokens), so this is a safe
    conservative upper bound for any provider.  It is the default and the test
    double; provider-specific estimators are free to tighten it.
    """

    def estimate(self, text: str) -> int:
        return len(text.encode("utf-8")) if text else 0


@dataclass(frozen=True, slots=True)
class DeepSeekOfficialTokenEstimator:
    """Conservative token-count estimate for DeepSeek-Official content.

    The priority is a provably-safe upper bound (never under-counts): the Agent
    core fail-closes on the *estimated* request exceeding the model input
    budget, so an under-estimate would let an over-budget request reach the
    provider.  DeepSeek's official offline tokenizer is not a fit for this
    service's dependency footprint — ``tiktoken`` / ``transformers`` /
    ``sentencepiece`` are not declared dependencies and the official BPE model
    file is a multi-megabyte binary that would have to be vendored or downloaded
    at runtime — so the estimator falls back to the byte bound.

    A token in any BPE tokenizer (byte- or character-level) covers at least one
    UTF-8 byte, so the real token count never exceeds the UTF-8 byte count: the
    maximum-token case is every byte (or every character) forming its own token
    with no merges.  ``len(text.encode("utf-8"))`` is therefore a safe upper
    bound for ASCII, JSON, CJK, emoji and control-heavy content alike.  It
    over-estimates CJK ~3x (one char -> one token, three bytes) and merged
    English prose; that conservatism is the cost of never under-counting without
    the offline tokenizer, and it is correct here, not a hole.

    ``bytes / 3`` was previously used to tighten the CJK case, but it is *not* a
    safe upper bound: ASCII, digit, punctuation and control-character runs can
    tokenize one-token-per-byte, which ``bytes / 3`` under-counts by up to 3x.
    """

    def estimate(self, text: str) -> int:
        return len(text.encode("utf-8")) if text else 0


def effective_candidate_estimator(
    candidates: tuple[ProviderCandidate, ...],
) -> TokenEstimator:
    """The estimator for the first candidate's provider type, fail-closed.

    Failover candidates all share the approved provider type (the registry is
    closed at ``AiProviderAdapterRegistry``), so the first candidate's
    provider type selects the estimator for the whole chain.  An empty chain
    has no provider, so the conservative ``ByteTokenEstimator`` is returned —
    the budget it produces is irrelevant because no chat can be made without a
    candidate, and the worker's context build only runs when a snapshot exists.
    """

    if not candidates:
        return ByteTokenEstimator()
    if candidates[0].provider_type is ProviderType.DEEPSEEK_OFFICIAL:
        return DeepSeekOfficialTokenEstimator()
    return ByteTokenEstimator()


class AiProviderAdapter(Protocol):
    async def list_models(
        self, encrypted_api_key: str, timeout_seconds: int
    ) -> tuple[ProviderModelInfo, ...]: ...

    async def chat(
        self, candidate: ProviderCandidate, request: ProviderChatRequest
    ) -> ProviderChatResponse: ...


class AiProviderAdapterRegistry:
    """Closed V1 registry: production cannot select an unapproved adapter type."""

    def __init__(self, adapters: Mapping[ProviderType, AiProviderAdapter]) -> None:
        copied = dict(adapters)
        if set(copied) != {ProviderType.DEEPSEEK_OFFICIAL}:
            raise ValueError("PROVIDER_V2_REGISTRY_INVALID")
        self._adapters: Mapping[ProviderType, AiProviderAdapter] = MappingProxyType(copied)

    def adapter_for(self, provider_type: ProviderType) -> AiProviderAdapter:
        return self._adapters[provider_type]

    @property
    def provider_types(self) -> tuple[ProviderType, ...]:
        return tuple(self._adapters)


@dataclass(frozen=True, slots=True)
class ProviderTransportPolicy:
    """HTTP transport byte-safety limits, independent of model token capacity.

    These are *wire* caps — how many bytes the adapter will read from or send to
    the provider socket — not model context capacity (``ModelCapabilities`` /
    ``ContextBudget``, in tokens) and not the Agent product's context policy.  A
    request that already fits the token budget is well below these caps in bytes,
    so they never bind on legitimate large-context requests; they exist only as
    an independent fail-closed net against a runaway/malformed peer and an
    accidental per-request memory blow-up.  Exceeding either cap fails closed
    (``MALFORMED_RESPONSE`` / ``INVALID_REQUEST``); SSRF pinning, TLS and the
    outbound guard are unaffected.  Defaults carry a full DeepSeek V4 1M-context
    request and output (including thinking ``reasoning_content``) with margin.
    """

    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        if self.max_request_bytes <= 0 or self.max_response_bytes <= 0:
            raise ValueError("invalid provider transport policy")


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class ProviderHttpTransport(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> ProviderHttpResponse: ...


class DeepSeekOfficialHttpTransport:
    """Fixed-origin HTTPS transport with the approved outbound guard."""

    def __init__(
        self,
        guard: OutboundEndpointGuard | None = None,
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._guard = guard or OutboundEndpointGuard()
        self._max_response_bytes = max_response_bytes

    async def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> ProviderHttpResponse:
        try:
            resolved = await self._guard.resolve_provider(DEEPSEEK_OFFICIAL_ORIGIN)
            revalidated = await self._guard.revalidate(resolved)
            url = provider_subresource_url(resolved, path.lstrip("/"))
            return await asyncio.to_thread(
                self._request_sync,
                method,
                revalidated,
                url,
                dict(headers),
                body,
                timeout_seconds,
                self._max_response_bytes,
            )
        except OutboundSecurityError as error:
            del error
            raise ProviderError(
                ProviderErrorClassification.NETWORK,
                retryable=True,
                failover_allowed=True,
            ) from None
        except TimeoutError:
            raise ProviderError(
                ProviderErrorClassification.TIMEOUT,
                retryable=True,
                failover_allowed=True,
            ) from None
        except (OSError, http.client.HTTPException):
            raise ProviderError(
                ProviderErrorClassification.NETWORK,
                retryable=True,
                failover_allowed=True,
            ) from None

    @staticmethod
    def _request_sync(
        method: str,
        endpoint: ResolvedEndpoint,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> ProviderHttpResponse:
        parsed = urlsplit(url)
        if parsed.hostname != endpoint.hostname or parsed.port not in {None, endpoint.port}:
            raise OutboundSecurityError("OUTBOUND_DESTINATION_CHANGED")
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        connection = _PinnedHTTPSConnection(
            endpoint.hostname,
            endpoint.port,
            endpoint.connection_address,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        )
        try:
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            return ProviderHttpResponse(
                response.status,
                response.read(max_response_bytes + 1),
                dict(response.headers.items()),
            )
        finally:
            connection.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Use the validated IP while retaining the official hostname for Host/SNI/TLS."""

    def __init__(
        self,
        host: str,
        port: int,
        connection_address: str,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._connection_address = connection_address
        self._pinned_context = context
        self._pinned_timeout = timeout

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._connection_address, self.port), timeout=self._pinned_timeout
        )
        self.sock = self._pinned_context.wrap_socket(self.sock, server_hostname=self.host)


class DeepSeekOfficialAdapter:
    """DeepSeek wire-format owner; no raw transport fields cross this class."""

    def __init__(
        self,
        cipher: SecretCipher,
        transport: ProviderHttpTransport | None = None,
        *,
        transport_policy: ProviderTransportPolicy | None = None,
    ) -> None:
        self._cipher = cipher
        self._transport_policy = transport_policy or ProviderTransportPolicy()
        self._transport = transport or DeepSeekOfficialHttpTransport(
            max_response_bytes=self._transport_policy.max_response_bytes
        )

    async def list_models(
        self, encrypted_api_key: str, timeout_seconds: int
    ) -> tuple[ProviderModelInfo, ...]:
        response = await self._request(
            "GET", "/models", encrypted_api_key, None, timeout_seconds
        )
        value = self._json_object(response.body)
        data = value.get("data")
        if not isinstance(data, list):
            raise self._malformed()
        models: list[ProviderModelInfo] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise self._malformed()
            model_id = item["id"].strip()
            if not model_id or len(model_id) > 128:
                raise self._malformed()
            models.append(ProviderModelInfo(model_id))
        return tuple(models)

    async def chat(
        self, candidate: ProviderCandidate, request: ProviderChatRequest
    ) -> ProviderChatResponse:
        started = asyncio.get_running_loop().time()
        payload = self._chat_payload(candidate.model_name, request)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > self._transport_policy.max_request_bytes:
            raise ProviderError(
                ProviderErrorClassification.INVALID_REQUEST,
                retryable=False,
                failover_allowed=False,
            )
        response = await self._request(
            "POST",
            "/chat/completions",
            candidate.encrypted_api_key,
            encoded,
            candidate.timeout_seconds,
        )
        value = self._json_object(response.body)
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise self._malformed()
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise self._malformed()
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise self._malformed()
        reasoning_content = message.get("reasoning_content")
        if reasoning_content is not None and not isinstance(reasoning_content, str):
            raise self._malformed()
        tool_calls = self._tool_calls(message.get("tool_calls", []))
        if not content and not tool_calls:
            raise self._malformed()
        finish = choice.get("finish_reason")
        if not isinstance(finish, str):
            raise self._malformed()
        usage = self._usage(value.get("usage", {}))
        return ProviderChatResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=self._finish_reason(finish),
            usage=usage,
            latency_ms=max(0, round((asyncio.get_running_loop().time() - started) * 1000)),
            reasoning_content=reasoning_content,
        )

    async def _request(
        self,
        method: str,
        path: str,
        encrypted_api_key: str,
        body: bytes | None,
        timeout_seconds: int,
    ) -> ProviderHttpResponse:
        try:
            key = self._cipher.decrypt(encrypted_api_key)
        except SecretCryptoError:
            raise ProviderError(
                ProviderErrorClassification.CREDENTIAL_UNAVAILABLE,
                retryable=False,
                failover_allowed=False,
            ) from None
        response = await self._transport.request(
            method,
            path,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            body,
            timeout_seconds,
        )
        if len(response.body) > self._transport_policy.max_response_bytes:
            raise self._malformed()
        if not 200 <= response.status_code < 300:
            raise self._http_error(response)
        return response

    @staticmethod
    def _chat_payload(model_name: str, request: ProviderChatRequest) -> dict[str, object]:
        return _canonical_chat_payload(request, model_name)

    @classmethod
    def _tool_calls(cls, value: object) -> tuple[ProviderToolCall, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise cls._malformed()
        result: list[ProviderToolCall] = []
        for item in value:
            if not isinstance(item, dict) or item.get("type") != "function":
                raise cls._malformed()
            function = item.get("function")
            if (
                not isinstance(item.get("id"), str)
                or not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or not isinstance(function.get("arguments"), str)
            ):
                raise cls._malformed()
            try:
                arguments = json.loads(function["arguments"])
            except (TypeError, ValueError, json.JSONDecodeError):
                raise cls._malformed() from None
            if not isinstance(arguments, dict):
                raise cls._malformed()
            result.append(
                ProviderToolCall(item["id"], function["name"], cls._json_mapping(arguments))
            )
        return tuple(result)

    @staticmethod
    def _json_mapping(value: Mapping[str, object]) -> Mapping[str, JSONValue]:
        # The JSON decoder already constrained the recursive runtime values.
        return value  # type: ignore[return-value]

    @classmethod
    def _usage(cls, value: object) -> ProviderTokenUsage:
        if not isinstance(value, dict):
            raise cls._malformed()
        input_tokens = value.get("prompt_tokens", 0)
        output_tokens = value.get("completion_tokens", 0)
        if any(type(item) is not int for item in (input_tokens, output_tokens)):
            raise cls._malformed()
        total_tokens = value.get("total_tokens", input_tokens + output_tokens)
        if type(total_tokens) is not int:
            raise cls._malformed()
        if min(input_tokens, output_tokens, total_tokens) < 0:
            raise cls._malformed()
        return ProviderTokenUsage(input_tokens, output_tokens, total_tokens)

    @staticmethod
    def _finish_reason(value: str) -> ProviderFinishReason:
        return {
            "stop": ProviderFinishReason.STOP,
            "tool_calls": ProviderFinishReason.TOOL_CALLS,
            "length": ProviderFinishReason.LENGTH,
            "content_filter": ProviderFinishReason.CONTENT_FILTER,
        }.get(value, ProviderFinishReason.OTHER)

    @classmethod
    def _json_object(cls, body: bytes) -> dict[str, object]:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise cls._malformed() from None
        if not isinstance(value, dict):
            raise cls._malformed()
        return value

    @classmethod
    def _http_error(cls, response: ProviderHttpResponse) -> ProviderError:
        status = response.status_code
        retry_after = cls._retry_after(
            next(
                (
                    value
                    for name, value in response.headers.items()
                    if name.lower() == "retry-after"
                ),
                None,
            )
        )
        if status == 401:
            return ProviderError(
                ProviderErrorClassification.AUTHENTICATION,
                retryable=False,
                failover_allowed=False,
                status_code=status,
            )
        if status == 403:
            return ProviderError(
                ProviderErrorClassification.PERMISSION,
                retryable=False,
                failover_allowed=False,
                status_code=status,
            )
        if status == 404:
            return ProviderError(
                ProviderErrorClassification.MODEL_NOT_FOUND,
                retryable=False,
                failover_allowed=True,
                status_code=status,
            )
        if status == 429:
            return ProviderError(
                ProviderErrorClassification.RATE_LIMITED,
                retryable=True,
                failover_allowed=True,
                status_code=status,
                retry_after_seconds=retry_after,
            )
        if status == 408:
            return ProviderError(
                ProviderErrorClassification.TIMEOUT,
                retryable=True,
                failover_allowed=True,
                status_code=status,
            )
        if 500 <= status <= 599:
            return ProviderError(
                ProviderErrorClassification.TRANSIENT_SERVER,
                retryable=True,
                failover_allowed=True,
                status_code=status,
                retry_after_seconds=retry_after,
            )
        return ProviderError(
            ProviderErrorClassification.INVALID_REQUEST,
            retryable=False,
            failover_allowed=False,
            status_code=status,
        )

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value.strip())
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                seconds = (parsed - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return min(seconds, MAX_RETRY_AFTER_SECONDS)

    @staticmethod
    def _malformed() -> ProviderError:
        return ProviderError(
            ProviderErrorClassification.MALFORMED_RESPONSE,
            retryable=False,
            failover_allowed=False,
        )


__all__ = [
    "DEEPSEEK_OFFICIAL_ORIGIN",
    "AiProviderAdapter",
    "AiProviderAdapterRegistry",
    "ByteTokenEstimator",
    "DeepSeekOfficialAdapter",
    "DeepSeekOfficialHttpTransport",
    "DeepSeekOfficialTokenEstimator",
    "ModelCapabilities",
    "ProviderCandidate",
    "ProviderCandidatesExhausted",
    "ProviderChatRequest",
    "ProviderChatResponse",
    "ProviderError",
    "ProviderErrorClassification",
    "ProviderFinishReason",
    "ProviderHttpResponse",
    "ProviderHttpTransport",
    "ProviderMessage",
    "ProviderModelInfo",
    "ProviderResponseFormat",
    "ProviderRole",
    "ProviderTokenUsage",
    "ProviderToolCall",
    "ProviderToolDefinition",
    "ProviderTransportPolicy",
    "ProviderType",
    "TokenEstimator",
    "effective_candidate_capabilities",
    "effective_candidate_estimator",
    "measure_provider_request",
    "measure_provider_request_tokens",
]
