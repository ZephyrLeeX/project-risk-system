"""Protocol-aware AI provider transport behind the shared outbound guard."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from risk_platform.ai_providers.models import AiProviderProtocol
from risk_platform.shared.outbound import (
    OutboundEndpointGuard,
    OutboundSecurityError,
    provider_subresource_url,
)

_ANTHROPIC_VERSION = "2023-06-01"
MAX_RETRY_AFTER_SECONDS = 10.0
AGENT_RESPONSE_TRANSPORT_RETRY_COUNT = 2
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConnectionOutcome:
    success: bool
    latency_ms: int
    error_code: str | None
    error_summary: str | None


@dataclass(frozen=True, slots=True)
class ProviderCompletionResult:
    text: str
    usage: dict[str, int]
    latency_ms: int


@dataclass(frozen=True, slots=True)
class ProviderResponseResult:
    """Decoded Responses transport envelope, before application normalization."""

    value: dict[str, object]
    usage: dict[str, int]
    latency_ms: int


@dataclass(frozen=True, slots=True)
class HttpResult:
    """The narrowly-scoped upstream metadata needed for retry decisions."""

    status_code: int
    body: str
    retry_after_seconds: float | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, request: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


class AiProviderClient:
    def __init__(self, guard: OutboundEndpointGuard | None = None) -> None:
        self._guard = guard or OutboundEndpointGuard()

    async def test(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: int,
        retry_count: int,
        protocol: AiProviderProtocol = AiProviderProtocol.OPENAI_CHAT_COMPLETIONS,
    ) -> ConnectionOutcome:
        started = time.monotonic()
        try:
            if protocol is AiProviderProtocol.OPENAI_CHAT_COMPLETIONS:
                resolved = await self._guard.resolve_provider(endpoint)
                await self._guard.revalidate(resolved)
                response = self._coerce_http_result(
                    await asyncio.to_thread(
                        self._request,
                        provider_subresource_url(resolved, "models"),
                        self._headers(protocol, api_key),
                        None,
                        timeout_seconds,
                    )
                )
                if not 200 <= response.status_code < 300:
                    raise self._http_error(response.status_code, response.retry_after_seconds)
            elif protocol is AiProviderProtocol.OPENAI_RESPONSES:
                await self._test_completion_endpoint(
                    endpoint, protocol, model, api_key, timeout_seconds, retry_count
                )
            else:
                await self.complete(
                    endpoint,
                    protocol,
                    model,
                    api_key,
                    {"connection_test": True},
                    timeout_seconds,
                    retry_count,
                    test_mode=True,
                )
            return ConnectionOutcome(True, self._elapsed(started), None, None)
        except OutboundSecurityError as error:
            return ConnectionOutcome(False, 0, error.code, "AI服务地址未通过出站安全校验")
        except ProviderRequestError as error:
            return ConnectionOutcome(
                False,
                self._elapsed(started),
                error.code,
                self._summary(error.code, timeout_seconds),
            )

    async def extract_risks(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: int,
        payload: Mapping[str, object],
        protocol: AiProviderProtocol = AiProviderProtocol.OPENAI_CHAT_COMPLETIONS,
    ) -> tuple[str, dict[str, int], int]:
        result = await self.complete(
            endpoint, protocol, model, api_key, payload, timeout_seconds, 0
        )
        return result.text, result.usage, result.latency_ms

    async def complete(
        self,
        endpoint: str,
        protocol: AiProviderProtocol,
        model: str,
        api_key: str,
        payload: Mapping[str, object],
        timeout_seconds: int,
        retry_count: int,
        *,
        test_mode: bool = False,
        phase: str | None = None,
        backoff: bool = False,
    ) -> ProviderCompletionResult:
        resolved = await self._guard.resolve_provider(endpoint)
        url = provider_subresource_url(resolved, self._operation(protocol))
        started, last = time.monotonic(), None
        for attempt in range(retry_count + 1):
            try:
                await self._guard.revalidate(resolved)
                response = self._coerce_http_result(
                    await asyncio.to_thread(
                        self._request,
                        url,
                        self._headers(protocol, api_key),
                        self._payload(protocol, model, payload, test_mode),
                        timeout_seconds,
                    )
                )
                if not 200 <= response.status_code < 300:
                    raise self._http_error(response.status_code, response.retry_after_seconds)
                return ProviderCompletionResult(
                    *self._parse(protocol, response.body), self._elapsed(started)
                )
            except TimeoutError:
                last = ProviderRequestError("UPSTREAM_TIMEOUT", retryable=True)
            except urllib.error.URLError:
                last = ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)
            except (OSError, ValueError):
                last = ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)
            except ProviderRequestError as error:
                last = error
            if last is not None and (not last.retryable or attempt == retry_count):
                raise last
            if backoff:
                assert last is not None
                await self._backoff(last, attempt + 1, phase)
        raise AssertionError("unreachable")

    async def complete_response(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        payload: Mapping[str, object],
        timeout_seconds: int,
        *,
        retry_count: int = AGENT_RESPONSE_TRANSPORT_RETRY_COUNT,
        phase: str | None = None,
    ) -> ProviderResponseResult:
        """Call a Responses endpoint without assuming its output is text.

        This is intentionally separate from ``complete``: callers that need native
        function calls must inspect the transport item types before normalizing them.
        """
        resolved = await self._guard.resolve_provider(endpoint)
        started = time.monotonic()
        url = provider_subresource_url(
            resolved, self._operation(AiProviderProtocol.OPENAI_RESPONSES)
        )
        last: ProviderRequestError | None = None
        for attempt in range(retry_count + 1):
            try:
                await self._guard.revalidate(resolved)
                result = self._coerce_http_result(
                    await asyncio.to_thread(
                        self._request,
                        url,
                        self._headers(AiProviderProtocol.OPENAI_RESPONSES, api_key),
                        self._payload(AiProviderProtocol.OPENAI_RESPONSES, model, payload, False),
                        timeout_seconds,
                    )
                )
                if not 200 <= result.status_code < 300:
                    if self._has_native_tools(payload) and self._native_tools_unsupported(
                        result.status_code, result.body
                    ):
                        raise ProviderRequestError(
                            "NATIVE_TOOLS_UNSUPPORTED",
                            retryable=False,
                            status_code=result.status_code,
                        )
                    raise self._http_error(result.status_code, result.retry_after_seconds)
                value: Any = json.loads(result.body)
                if not isinstance(value, dict) or not isinstance(value.get("output"), list):
                    raise ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False)
                usage = value.get("usage", {})
                if not isinstance(usage, dict):
                    usage = {}
                return ProviderResponseResult(
                    value,
                    {
                        "input": int(usage.get("input_tokens", 0)),
                        "output": int(usage.get("output_tokens", 0)),
                        "total": int(usage.get("total_tokens", 0)),
                    },
                    self._elapsed(started),
                )
            except TimeoutError:
                last = ProviderRequestError("UPSTREAM_TIMEOUT", retryable=True)
            except urllib.error.URLError:
                last = ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)
            except OSError:
                last = ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)
            except ProviderRequestError as error:
                last = error
            except (TypeError, ValueError, json.JSONDecodeError):
                last = ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False)
            if last is not None and (not last.retryable or attempt == retry_count):
                raise last
            assert last is not None
            await self._backoff(last, attempt + 1, phase)
        raise AssertionError("unreachable")

    async def _test_completion_endpoint(
        self,
        endpoint: str,
        protocol: AiProviderProtocol,
        model: str,
        api_key: str,
        timeout_seconds: int,
        retry_count: int,
    ) -> None:
        """Verify a completion endpoint without requiring a business-ready response.

        Normal completions must yield usable text and continue through ``_parse``.
        A connection test only establishes that the endpoint accepted a valid request.
        """
        resolved = await self._guard.resolve_provider(endpoint)
        url, last = provider_subresource_url(resolved, self._operation(protocol)), None
        for attempt in range(retry_count + 1):
            try:
                await self._guard.revalidate(resolved)
                response = self._coerce_http_result(
                    await asyncio.to_thread(
                        self._request,
                        url,
                        self._headers(protocol, api_key),
                        self._payload(protocol, model, {"connection_test": True}, True),
                        timeout_seconds,
                    )
                )
                if not 200 <= response.status_code < 300:
                    raise self._http_error(response.status_code, response.retry_after_seconds)
                if protocol is AiProviderProtocol.OPENAI_RESPONSES:
                    self._validate_responses_connection_response(response.body)
                else:
                    self._parse(protocol, response.body)
                return
            except TimeoutError:
                last = ProviderRequestError("UPSTREAM_TIMEOUT", retryable=True)
            except urllib.error.URLError:
                last = ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)
            except (OSError, ValueError):
                last = ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)
            except ProviderRequestError as error:
                last = error
            if last is not None and (not last.retryable or attempt == retry_count):
                raise last
        raise AssertionError("unreachable")

    @staticmethod
    def _operation(protocol: AiProviderProtocol) -> str:
        return {
            AiProviderProtocol.OPENAI_CHAT_COMPLETIONS: "chat/completions",
            AiProviderProtocol.OPENAI_RESPONSES: "responses",
            AiProviderProtocol.ANTHROPIC_MESSAGES: "messages",
        }[protocol]

    @staticmethod
    def _headers(protocol: AiProviderProtocol, api_key: str) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if protocol is AiProviderProtocol.ANTHROPIC_MESSAGES:
            headers.update({"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION})
        else:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _payload(
        protocol: AiProviderProtocol, model: str, payload: Mapping[str, object], test_mode: bool
    ) -> dict[str, object]:
        system_instruction, data_payload = AiProviderClient._separate_system_instruction(payload)
        content = "ping" if test_mode else json.dumps(data_payload, ensure_ascii=False)
        if protocol is AiProviderProtocol.OPENAI_CHAT_COMPLETIONS:
            messages: list[dict[str, str]] = [{"role": "user", "content": content}]
            if system_instruction is not None and not test_mode:
                messages.insert(0, {"role": "system", "content": system_instruction})
            return {
                "model": model,
                "temperature": 0,
                "messages": messages,
            }
        if protocol is AiProviderProtocol.OPENAI_RESPONSES:
            request: dict[str, object] = {
                "model": model,
                "input": content,
                "max_output_tokens": 256 if test_mode else 1024,
                "store": False,
            }
            if system_instruction is not None and not test_mode:
                request["instructions"] = system_instruction
            native_tools = data_payload.pop("_responsesNativeTools", None)
            if native_tools is not None and not test_mode:
                request["input"] = json.dumps(data_payload, ensure_ascii=False)
                request["tools"] = native_tools
            return request
        messages = [{"role": "user", "content": content}]
        request = {
            "model": model,
            "max_tokens": 16 if test_mode else 1024,
            "temperature": 0,
            "messages": messages,
        }
        if system_instruction is not None and not test_mode:
            request["system"] = system_instruction
        return request

    @staticmethod
    def _separate_system_instruction(
        payload: Mapping[str, object],
    ) -> tuple[str | None, dict[str, object]]:
        """Keep Agent policy in the provider's trusted instruction channel."""
        data_payload = dict(payload)
        instruction = data_payload.pop("systemInstruction", None)
        return instruction if isinstance(instruction, str) else None, data_payload

    @staticmethod
    def _parse(protocol: AiProviderProtocol, body: str) -> tuple[str, dict[str, int]]:
        try:
            value: Any = json.loads(body)
            if protocol is AiProviderProtocol.OPENAI_CHAT_COMPLETIONS:
                text, usage = value["choices"][0]["message"]["content"], value.get("usage", {})
                tokens = {
                    "input": int(usage.get("prompt_tokens", 0)),
                    "output": int(usage.get("completion_tokens", 0)),
                    "total": int(usage.get("total_tokens", 0)),
                }
            elif protocol is AiProviderProtocol.OPENAI_RESPONSES:
                text = "".join(
                    block["text"]
                    for item in value["output"]
                    if item.get("type") == "message"
                    for block in item.get("content", [])
                    if block.get("type") == "output_text"
                )
                usage = value.get("usage", {})
                tokens = {
                    "input": int(usage.get("input_tokens", 0)),
                    "output": int(usage.get("output_tokens", 0)),
                    "total": int(usage.get("total_tokens", 0)),
                }
            else:
                text = "".join(
                    block["text"] for block in value["content"] if block.get("type") == "text"
                )
                usage = value.get("usage", {})
                tokens = {
                    "input": int(usage.get("input_tokens", 0)),
                    "output": int(usage.get("output_tokens", 0)),
                }
                tokens["total"] = tokens["input"] + tokens["output"]
            if not isinstance(text, str) or not text:
                raise ValueError
            return text, tokens
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False) from None

    @staticmethod
    def _validate_responses_connection_response(body: str) -> None:
        """Accept the minimum successful Responses API envelope for a connection test."""
        try:
            value: Any = json.loads(body)
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("status"), str)
                or not isinstance(value.get("output"), list)
            ):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False) from None

    @staticmethod
    def _request(
        url: str, headers: dict[str, str], payload: dict[str, object] | None, timeout_seconds: int
    ) -> HttpResult:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=timeout_seconds
            ) as response:
                return HttpResult(
                    int(response.status),
                    response.read(128 * 1024).decode("utf-8"),
                    AiProviderClient._retry_after_seconds(response.headers.get("Retry-After")),
                )
        except urllib.error.HTTPError as error:
            return HttpResult(
                error.code,
                error.read(64 * 1024).decode("utf-8", errors="replace"),
                AiProviderClient._retry_after_seconds(error.headers.get("Retry-After")),
            )

    @staticmethod
    def _http_error(
        status: int, retry_after_seconds: float | None = None
    ) -> ProviderRequestError:
        if status in {401, 403}:
            return ProviderRequestError(
                "AUTHENTICATION_FAILED", retryable=False, status_code=status
            )
        if status == 404:
            return ProviderRequestError("MODEL_NOT_FOUND", retryable=False, status_code=status)
        if status == 429:
            return ProviderRequestError(
                "RATE_LIMITED",
                retryable=True,
                status_code=status,
                retry_after_seconds=retry_after_seconds,
            )
        if status == 408:
            return ProviderRequestError("UPSTREAM_TIMEOUT", retryable=True, status_code=status)
        if status >= 500:
            return ProviderRequestError(
                "HTTP_5XX",
                retryable=True,
                status_code=status,
                retry_after_seconds=retry_after_seconds,
            )
        if 400 <= status < 500:
            return ProviderRequestError("INVALID_REQUEST", retryable=False, status_code=status)
        return ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)

    @staticmethod
    def _coerce_http_result(value: HttpResult | tuple[int, str]) -> HttpResult:
        if isinstance(value, HttpResult):
            return value
        return HttpResult(*value)

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value.strip())
        except ValueError:
            return None
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return min(seconds, MAX_RETRY_AFTER_SECONDS)

    @staticmethod
    def _has_native_tools(payload: Mapping[str, object]) -> bool:
        return isinstance(payload.get("_responsesNativeTools"), list) and bool(
            payload["_responsesNativeTools"]
        )

    @staticmethod
    def _native_tools_unsupported(status: int, body: str) -> bool:
        """Recognize only explicit, request-scoped tool-capability failures."""
        if status not in {400, 500}:
            return False
        message = body
        try:
            value = json.loads(body)
            if isinstance(value, dict):
                error = value.get("error", value)
                if isinstance(error, dict):
                    message = " ".join(
                        str(error.get(key, "")) for key in ("code", "type", "message")
                    )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        normalized = message.casefold()
        unsupported = ("unsupported", "not supported", "not implemented", "unimplemented")
        return "not implemented" in normalized or (
            any(marker in normalized for marker in unsupported)
            and any(marker in normalized for marker in ("tool", "function", "capability"))
        )

    async def _backoff(self, error: ProviderRequestError, attempt: int, phase: str | None) -> None:
        delay = error.retry_after_seconds
        if delay is None:
            delay = min(
                float(2 ** (attempt - 1)) + random.uniform(0, 0.25),
                MAX_RETRY_AFTER_SECONDS,
            )
        logger.info(
            "agent provider retry phase=%s reason=%s attempt=%s backoff_ms=%s status_code=%s",
            phase or "UNKNOWN",
            error.code,
            attempt,
            int(delay * 1000),
            error.status_code,
        )
        await asyncio.sleep(delay)

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    @staticmethod
    def _summary(code: str, timeout: int) -> str:
        return {
            "AUTHENTICATION_FAILED": "AI服务认证失败",
            "RATE_LIMITED": "AI服务请求过于频繁",
            "MODEL_NOT_FOUND": "模型或协议端点不存在",
            "INVALID_REQUEST": "AI服务拒绝了请求",
            "UPSTREAM_TIMEOUT": f"连接测试超过{timeout}秒",
            "PROVIDER_INVALID_OUTPUT": "AI服务返回格式无效",
        }.get(code, "无法连接AI服务地址")


class ProviderRequestError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(code)
        self.code, self.retryable, self.status_code = code, retryable, status_code
        self.retry_after_seconds = retry_after_seconds


__all__ = [
    "AGENT_RESPONSE_TRANSPORT_RETRY_COUNT",
    "AiProviderClient",
    "ConnectionOutcome",
    "HttpResult",
    "ProviderCompletionResult",
    "ProviderRequestError",
    "ProviderResponseResult",
]
