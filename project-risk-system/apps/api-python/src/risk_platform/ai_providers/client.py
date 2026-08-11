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

    @staticmethod
    def _request(url: str, api_key: str, timeout_seconds: int) -> tuple[int, str]:
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        )
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.status), response.read(64 * 1024).decode("utf-8")


__all__ = ["AiProviderClient", "ConnectionOutcome"]
