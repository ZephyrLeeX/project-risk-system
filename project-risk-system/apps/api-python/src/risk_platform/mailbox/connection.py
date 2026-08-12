from __future__ import annotations

import asyncio
import email.utils
import imaplib
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email.parser import BytesHeaderParser

from risk_platform.shared.outbound import OutboundEndpointGuard


@dataclass(frozen=True, slots=True)
class ConnectionOutcome:
    success: bool
    latency_ms: int
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class MailEnvelope:
    uid: int
    uid_validity: int
    message_id: str | None
    subject: str | None
    sender: str | None
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class MailSyncSnapshot:
    uid_validity: int
    envelopes: tuple[MailEnvelope, ...]


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

    async def discover(
        self,
        *,
        email: str,
        auth_code: str,
        host: str,
        port: int,
        encryption: str,
        folder: str,
        cursor: int | None,
        initial_sync_weeks: int,
    ) -> MailSyncSnapshot:
        """Fetch envelope metadata only; message bodies never leave this worker."""

        endpoint = await self._outbound.resolve_imap(host, port)
        client: object | None = None
        try:
            client = await asyncio.to_thread(
                self._create, endpoint.connection_address, host, port, encryption
            )
            await asyncio.to_thread(self._login, client, email, auth_code)
            await asyncio.to_thread(self._select_readonly, client, folder)
            validity = await asyncio.to_thread(self._uid_validity, client)
            criterion = (
                f"UID {cursor + 1}:*"
                if cursor is not None
                else f"SINCE {self._since(initial_sync_weeks)}"
            )
            uids = await asyncio.to_thread(self._search, client, criterion)
            envelopes = []
            for uid in uids:
                raw = await asyncio.to_thread(self._fetch_header, client, uid)
                envelopes.append(self._parse_envelope(uid, validity, raw))
            return MailSyncSnapshot(validity, tuple(envelopes))
        finally:
            if client is not None:
                await asyncio.to_thread(self._close, client)

    async def fetch_source(
        self,
        *,
        email: str,
        auth_code: str,
        host: str,
        port: int,
        encryption: str,
        folder: str,
        uid_validity: int,
        imap_uid: int,
    ) -> bytes:
        """Re-fetch one UID source; no caller may persist its returned bytes."""

        endpoint = await self._outbound.resolve_imap(host, port)
        client: object | None = None
        try:
            client = await asyncio.to_thread(
                self._create, endpoint.connection_address, host, port, encryption
            )
            await asyncio.to_thread(self._login, client, email, auth_code)
            await asyncio.to_thread(self._select_readonly, client, folder)
            if await asyncio.to_thread(self._uid_validity, client) != uid_validity:
                raise MailSourceUnavailable("UIDVALIDITY_CHANGED")
            return await asyncio.to_thread(self._fetch_source, client, imap_uid)
        finally:
            if client is not None:
                await asyncio.to_thread(self._close, client)

    @staticmethod
    def _since(weeks: int) -> str:
        from datetime import timedelta

        return (datetime.now(UTC) - timedelta(weeks=weeks)).strftime("%d-%b-%Y")

    @staticmethod
    def _uid_validity(client: object) -> int:
        typ, data = client.response("UIDVALIDITY")  # type: ignore[attr-defined]
        if typ != "UIDVALIDITY" or not data or not data[0]:
            raise RuntimeError("IMAP UIDVALIDITY unavailable")
        return int(data[0])

    @staticmethod
    def _search(client: object, criterion: str) -> list[int]:
        typ, data = client.uid("search", None, criterion)  # type: ignore[attr-defined]
        if typ != "OK" or not data or not data[0]:
            return []
        return sorted(int(value) for value in data[0].split())

    @staticmethod
    def _fetch_header(client: object, uid: int) -> bytes:
        typ, data = client.uid("fetch", str(uid), "(UID BODY.PEEK[HEADER])")  # type: ignore[attr-defined]
        if typ != "OK":
            raise RuntimeError("IMAP message fetch failed")
        return b"".join(item for item in data if isinstance(item, bytes))

    @staticmethod
    def _fetch_source(client: object, uid: int) -> bytes:
        typ, data = client.uid("fetch", str(uid), "(UID RFC822.SIZE BODY.PEEK[])")  # type: ignore[attr-defined]
        if typ != "OK" or not data:
            raise MailSourceUnavailable("MAIL_SOURCE_MISSING")
        source = b"".join(item for item in data if isinstance(item, bytes))
        if not source:
            raise MailSourceUnavailable("MAIL_SOURCE_MISSING")
        return source

    @staticmethod
    def _parse_envelope(uid: int, validity: int, raw: bytes) -> MailEnvelope:
        header = BytesHeaderParser().parsebytes(raw)
        parsed = (
            email.utils.parsedate_to_datetime(header.get("Date", ""))
            if header.get("Date")
            else None
        )
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return MailEnvelope(
            uid=uid,
            uid_validity=validity,
            message_id=header.get("Message-ID"),
            subject=header.get("Subject"),
            sender=header.get("From"),
            sent_at=parsed,
        )

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


class MailSourceUnavailable(RuntimeError):
    """A UID source cannot be re-fetched and is terminal under ADR 0022."""


__all__ = ["ConnectionOutcome", "MailSourceUnavailable", "MailboxConnection"]
