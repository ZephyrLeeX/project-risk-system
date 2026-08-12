"""Real OpenAI-compatible provider connectivity check."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from risk_platform.shared.outbound import (
    OutboundEndpointGuard,
    OutboundSecurityError,
    provider_subresource_url,
)


@dataclass(frozen=True, slots=True)
class ConnectionOutcome:
    success: bool
    latency_ms: int
    error_code: str | None
    error_summary: str | None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, request: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


class AiProviderClient:
    def __init__(self, guard: OutboundEndpointGuard | None = None) -> None:
        self._guard = guard or OutboundEndpointGuard()

    async def test(
        self, endpoint: str, model: str, api_key: str, timeout_seconds: int, retry_count: int
    ) -> ConnectionOutcome:
        started = time.monotonic()
        try:
            resolved = await self._guard.resolve_provider(endpoint)
            url = provider_subresource_url(resolved, "models")
        except OutboundSecurityError as error:
            return ConnectionOutcome(False, 0, error.code, "AI服务地址未通过出站安全校验")
        last_code, last_summary = "UPSTREAM_UNREACHABLE", "无法连接AI服务地址"
        for _ in range(retry_count + 1):
            try:
                await self._guard.revalidate(resolved)
                status, body = await asyncio.to_thread(self._request, url, api_key, timeout_seconds)
                if 200 <= status < 300:
                    json.loads(body)
                    return ConnectionOutcome(
                        True, int((time.monotonic() - started) * 1000), None, None
                    )
                last_code, last_summary = f"HTTP_{status}", f"上游服务返回 HTTP {status}"
            except TimeoutError:
                last_code, last_summary = "UPSTREAM_TIMEOUT", f"连接测试超过{timeout_seconds}秒"
            except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
                last_code, last_summary = "UPSTREAM_UNREACHABLE", "无法连接AI服务地址"
        return ConnectionOutcome(
            False, int((time.monotonic() - started) * 1000), last_code, last_summary
        )

    async def extract_risks(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: int,
        payload: dict[str, object],
    ) -> tuple[str, dict[str, int], int]:
        """Call the approved OpenAI-compatible extraction endpoint.

        ``payload`` is intentionally caller-owned and only exists for this request.
        No request/response content is retained by this client.
        """
        resolved = await self._guard.resolve_provider(endpoint)
        url = provider_subresource_url(resolved, "chat/completions")
        await self._guard.revalidate(resolved)
        started = time.monotonic()
        try:
            status, body = await asyncio.to_thread(
                self._completion_request, url, api_key, model, payload, timeout_seconds
            )
        except TimeoutError:
            raise ProviderRequestError("UPSTREAM_TIMEOUT", retryable=True) from None
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            raise ProviderRequestError("UPSTREAM_UNREACHABLE", retryable=True) from None
        if status in {408, 429} or status >= 500:
            raise ProviderRequestError(f"HTTP_{status}", retryable=True)
        if not 200 <= status < 300:
            raise ProviderRequestError(f"HTTP_{status}", retryable=False)
        try:
            value = json.loads(body)
            content = value["choices"][0]["message"]["content"]
            usage = value.get("usage", {})
            if not isinstance(content, str) or not isinstance(usage, dict):
                raise ValueError
            tokens = {
                "input": int(usage.get("prompt_tokens", 0)),
                "output": int(usage.get("completion_tokens", 0)),
                "total": int(usage.get("total_tokens", 0)),
            }
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise ProviderRequestError("PROVIDER_INVALID_OUTPUT", retryable=False) from None
        return content, tokens, int((time.monotonic() - started) * 1000)

    @staticmethod
    def _request(url: str, api_key: str, timeout_seconds: int) -> tuple[int, str]:
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        )
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.status), response.read(64 * 1024).decode("utf-8")

    @staticmethod
    def _completion_request(
        url: str, api_key: str, model: str, payload: dict[str, object], timeout_seconds: int
    ) -> tuple[int, str]:
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "model": model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": json.dumps(payload)}],
                },
                ensure_ascii=False,
            ).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                return int(response.status), response.read(128 * 1024).decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read(64 * 1024).decode("utf-8", errors="replace")


class ProviderRequestError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


__all__ = ["AiProviderClient", "ConnectionOutcome", "ProviderRequestError"]
