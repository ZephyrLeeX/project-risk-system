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
MAX_RESPONSE_BYTES = 128 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_RETRY_AFTER_SECONDS = 10.0


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


@dataclass(frozen=True, slots=True)
class ProviderModelInfo:
    id: str


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    account_id: UUID
    account_name: str
    provider_type: ProviderType
    model_config_id: UUID
    model_name: str
    timeout_seconds: int
    encrypted_api_key: str


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

    def __init__(self, guard: OutboundEndpointGuard | None = None) -> None:
        self._guard = guard or OutboundEndpointGuard()

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
                response.read(MAX_RESPONSE_BYTES + 1),
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
    ) -> None:
        self._cipher = cipher
        self._transport = transport or DeepSeekOfficialHttpTransport()

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
        if len(encoded) > MAX_REQUEST_BYTES:
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
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise self._malformed()
        if not 200 <= response.status_code < 300:
            raise self._http_error(response)
        return response

    @staticmethod
    def _chat_payload(model_name: str, request: ProviderChatRequest) -> dict[str, object]:
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
    "DeepSeekOfficialAdapter",
    "DeepSeekOfficialHttpTransport",
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
    "ProviderType",
]
