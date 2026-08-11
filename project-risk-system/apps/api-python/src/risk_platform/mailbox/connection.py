from __future__ import annotations

import asyncio
import imaplib
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from risk_platform.shared.outbound import OutboundEndpointGuard


@dataclass(frozen=True, slots=True)
class ConnectionOutcome:
    success: bool
    latency_ms: int
    error_code: str | None = None
    error_summary: str | None = None


class MailboxConnection:
    def __init__(
        self,
        client_factory: Callable[..., object] | None = None,
        outbound: OutboundEndpointGuard | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._outbound = outbound or OutboundEndpointGuard()

    async def test(
        self, *, email: str, auth_code: str, host: str, port: int, encryption: str, folder: str
    ) -> ConnectionOutcome:
        started = time.monotonic()
        client: object | None = None
        try:
            endpoint = await self._outbound.resolve_imap(host, port)
            client = await asyncio.to_thread(
                self._create, endpoint.connection_address, host, port, encryption
            )
            await asyncio.to_thread(self._login, client, email, auth_code)
            await asyncio.to_thread(self._select_readonly, client, folder)
            await asyncio.to_thread(self._logout, client)
            return ConnectionOutcome(True, int((time.monotonic() - started) * 1000))
        except Exception as exc:
            if client is not None:
                await asyncio.to_thread(self._close, client)
            code, summary = self.classify(exc)
            return ConnectionOutcome(False, int((time.monotonic() - started) * 1000), code, summary)

    def _create(self, resolved: str, host: str, port: int, encryption: str) -> object:
        if self._client_factory is not None:
            return self._client_factory(resolved, port, encryption, host)
        if encryption == "SSL":
            return imaplib.IMAP4_SSL(resolved, port, timeout=12)
        client = imaplib.IMAP4(resolved, port, timeout=12)
        client.starttls()
        return client

    @staticmethod
    def _login(client: object, email: str, auth_code: str) -> None:
        client.login(email, auth_code)  # type: ignore[attr-defined]

    @staticmethod
    def _select_readonly(client: object, folder: str) -> None:
        client.select(folder, readonly=True)  # type: ignore[attr-defined]

    @staticmethod
    def _logout(client: object) -> None:
        client.logout()  # type: ignore[attr-defined]

    @staticmethod
    def _close(client: object) -> None:
        with suppress(Exception):
            client.logout()  # type: ignore[attr-defined]

    @staticmethod
    def classify(error: Exception) -> tuple[str, str]:
        message = str(error)
        if any(word in message.lower() for word in ("auth", "login", "password", "credential")):
            return "AUTHENTICATION_FAILED", "邮箱地址或授权码验证失败"
        if any(word in message.lower() for word in ("mailbox", "folder", "not found")):
            return "FOLDER_NOT_FOUND", "无法访问所选邮件文件夹"
        if "timeout" in message.lower() or "timed out" in message.lower():
            return "CONNECTION_TIMEOUT", "连接IMAP服务器超时"
        if any(word in message.lower() for word in ("certificate", "tls", "ssl")):
            return "TLS_VERIFICATION_FAILED", "IMAP服务器TLS证书校验失败"
        return "IMAP_UNREACHABLE", "无法连接IMAP服务器或服务器拒绝访问"


__all__ = ["ConnectionOutcome", "MailboxConnection"]
