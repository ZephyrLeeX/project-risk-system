"""Protocol-aware AI provider transport behind the shared outbound guard."""

from __future__ import annotations

import asyncio
import json
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
                status, _ = await asyncio.to_thread(
                    self._request,
                    provider_subresource_url(resolved, "models"),
                    self._headers(protocol, api_key),
                    None,
                    timeout_seconds,
                )
                if not 200 <= status < 300:
                    raise self._http_error(status)
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
    ) -> ProviderCompletionResult:
        resolved = await self._guard.resolve_provider(endpoint)
        url = provider_subresource_url(resolved, self._operation(protocol))
        started, last = time.monotonic(), None
        for attempt in range(retry_count + 1):
            try:
                await self._guard.revalidate(resolved)
                status, body = await asyncio.to_thread(
                    self._request,
                    url,
                    self._headers(protocol, api_key),
                    self._payload(protocol, model, payload, test_mode),
                    timeout_seconds,
                )
                if not 200 <= status < 300:
                    raise self._http_error(status)
                return ProviderCompletionResult(
                    *self._parse(protocol, body), self._elapsed(started)
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
        content = "ping" if test_mode else json.dumps(payload, ensure_ascii=False)
        if protocol is AiProviderProtocol.OPENAI_CHAT_COMPLETIONS:
            return {
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user", "content": content}],
            }
        if protocol is AiProviderProtocol.OPENAI_RESPONSES:
            return {
                "model": model,
                "input": content,
                "max_output_tokens": 16 if test_mode else 1024,
                "store": False,
            }
        return {
            "model": model,
            "max_tokens": 16 if test_mode else 1024,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
        }

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
    def _request(
        url: str, headers: dict[str, str], payload: dict[str, object] | None, timeout_seconds: int
    ) -> tuple[int, str]:
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
                return int(response.status), response.read(128 * 1024).decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read(64 * 1024).decode("utf-8", errors="replace")

    @staticmethod
    def _http_error(status: int) -> ProviderRequestError:
        if status in {401, 403}:
            return ProviderRequestError(
                "AUTHENTICATION_FAILED", retryable=False, status_code=status
            )
        if status == 404:
            return ProviderRequestError("MODEL_NOT_FOUND", retryable=False, status_code=status)
        if status == 429:
            return ProviderRequestError("RATE_LIMITED", retryable=True, status_code=status)
        if status == 408:
            return ProviderRequestError("UPSTREAM_TIMEOUT", retryable=True, status_code=status)
        if status >= 500:
            return ProviderRequestError("HTTP_5XX", retryable=True, status_code=status)
        if 400 <= status < 500:
            return ProviderRequestError("INVALID_REQUEST", retryable=False, status_code=status)
        return ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True)

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
    def __init__(self, code: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code, self.retryable, self.status_code = code, retryable, status_code


__all__ = [
    "AiProviderClient",
    "ConnectionOutcome",
    "ProviderCompletionResult",
    "ProviderRequestError",
]
