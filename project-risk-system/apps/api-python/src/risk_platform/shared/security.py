"""Baseline request and Cookie security contracts."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Literal, TypedDict

from starlette.types import ASGIApp, Receive, Scope, Send

from risk_platform.config import IpNetwork, Settings


class SessionCookieOptions(TypedDict):
    httponly: bool
    secure: bool
    samesite: Literal["lax"]
    path: str


def session_cookie_options(settings: Settings) -> SessionCookieOptions:
    """Defaults consumed by the later authentication module."""

    return {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }


class TrustedProxyHeadersMiddleware:
    """Honor forwarding metadata only when the direct peer is explicitly trusted."""

    def __init__(self, app: ASGIApp, trusted_proxy_cidrs: tuple[IpNetwork, ...]) -> None:
        self.app = app
        self.trusted_proxy_cidrs = trusted_proxy_cidrs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._peer_is_trusted(scope):
            headers = {key.lower(): value for key, value in scope["headers"]}
            self._apply_forwarded_for(scope, headers.get(b"x-forwarded-for"))
            self._apply_forwarded_proto(scope, headers.get(b"x-forwarded-proto"))
        await self.app(scope, receive, send)

    def _peer_is_trusted(self, scope: Scope) -> bool:
        client = scope.get("client")
        if client is None:
            return False
        try:
            peer = ip_address(client[0])
        except ValueError:
            return False
        return any(peer in network for network in self.trusted_proxy_cidrs)

    def _apply_forwarded_for(self, scope: Scope, raw_value: bytes | None) -> None:
        if raw_value is None:
            return
        try:
            forwarded = [ip_address(item.strip()) for item in raw_value.decode("ascii").split(",")]
        except (UnicodeDecodeError, ValueError):
            return

        client = scope.get("client")
        peer_chain = [*forwarded, ip_address(client[0])] if client is not None else forwarded
        selected = peer_chain[0]
        for candidate in reversed(peer_chain):
            if not any(candidate in network for network in self.trusted_proxy_cidrs):
                selected = candidate
                break
        scope["client"] = (str(selected), client[1] if client is not None else 0)

    @staticmethod
    def _apply_forwarded_proto(scope: Scope, raw_value: bytes | None) -> None:
        if raw_value is None:
            return
        try:
            scheme = raw_value.decode("ascii").split(",")[-1].strip().lower()
        except UnicodeDecodeError:
            return
        if scheme in {"http", "https"}:
            scope["scheme"] = scheme


__all__ = [
    "SessionCookieOptions",
    "TrustedProxyHeadersMiddleware",
    "session_cookie_options",
]
