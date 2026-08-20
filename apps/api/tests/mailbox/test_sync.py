from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import cast

from risk_platform.mailbox.connection import MailboxConnection, MailSourceUnavailable
from risk_platform.mailbox.parsing import parse_mail
from risk_platform.shared.outbound import OutboundEndpointGuard


class FakeSyncImap:
    def login(self, email: str, auth_code: str) -> tuple[str, list[bytes]]:
        assert email == "owner@example.com"
        assert auth_code == "secret-code"
        return "OK", [b"logged in"]

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        assert folder == "INBOX"
        assert readonly is True
        return "OK", [b"0"]

    def response(self, name: str) -> tuple[str, list[bytes]]:
        assert name == "UIDVALIDITY"
        return "UIDVALIDITY", [b"42"]

    def uid(self, operation: str, *args: str) -> tuple[str, list[object]]:
        if operation == "search":
            return "OK", [b"7 8"]
        assert operation == "fetch"
        uid = args[0]
        return "OK", [
            (
                b'7 (UID ' + uid.encode() + b' INTERNALDATE "11-Aug-2026 18:30:00 +0800")',
                b"Message-ID: <id-"
                + uid.encode()
                + b">\r\nSubject: Weekly status\r\nFrom: sender@example.com\r\n"
                + b"Date: Tue, 11 Aug 2026 10:00:00 +0000\r\n\r\n",
            ),
            b")",
        ]

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b"logged out"]


async def fake_resolver(hostname: str, port: int) -> Sequence[str]:
    del hostname, port
    return ["93.184.216.34"]


def test_discover_returns_uid_only_envelopes_and_never_source_payload() -> None:
    connection = MailboxConnection(
        client_factory=lambda resolved, port, encryption, host: FakeSyncImap(),
        outbound=OutboundEndpointGuard(
            resolver=cast("Callable[[str, int], Awaitable[Sequence[str]]]", fake_resolver)
        ),
    )

    snapshot = asyncio.run(
        connection.discover(
            email="owner@example.com",
            auth_code="secret-code",
            host="imap.example.com",
            port=993,
            encryption="SSL",
            folder="INBOX",
            cursor=6,
            initial_sync_weeks=4,
        )
    )

    assert snapshot.uid_validity == 42
    assert [item.uid for item in snapshot.envelopes] == [7, 8]
    assert snapshot.envelopes[0].message_id == "<id-7>"
    assert snapshot.envelopes[0].sent_at == datetime(2026, 8, 11, 10, tzinfo=UTC)
    assert snapshot.envelopes[0].received_at == datetime(2026, 8, 11, 10, 30, tzinfo=UTC)
    assert not hasattr(snapshot.envelopes[0], "source")


def test_fetch_source_uses_only_real_imaplib_tuple_payload_and_round_trips_mail() -> None:
    message = EmailMessage()
    message["Subject"] = "[WSLDEMO][周报] ERP 系统升级本周进展与事项"
    message["From"] = "sender@example.com"
    message.set_content("ERP 系统升级本周进展正常，风险事项已跟进。")
    raw = message.as_bytes()

    class FetchClient:
        def uid(self, operation: str, uid: str, query: str) -> tuple[str, list[object]]:
            assert (operation, uid, query) == ("fetch", "295", "(UID RFC822.SIZE BODY.PEEK[])")
            return "OK", [(b"1 (UID 295 RFC822.SIZE BODY[] {%d}" % len(raw), raw), b")"]

    source = MailboxConnection._fetch_source(FetchClient(), 295)
    assert source == raw
    parsed = parse_mail(source, "<fallback>")
    assert parsed.subject == "[WSLDEMO][周报] ERP 系统升级本周进展与事项"
    assert "ERP 系统升级本周进展正常" in parsed.body_text


def test_fetch_source_rejects_empty_or_malformed_fetch() -> None:
    class FetchClient:
        def __init__(self, data: list[object], typ: str = "OK") -> None:
            self.data, self.typ = data, typ

        def uid(self, operation: str, uid: str, query: str) -> tuple[str, list[object]]:
            return self.typ, self.data

    for client in (
        FetchClient([]),
        FetchClient([b")"]),
        FetchClient([(b"metadata", b"")]),
        FetchClient([], typ="NO"),
    ):
        try:
            MailboxConnection._fetch_source(client, 295)
        except MailSourceUnavailable as error:
            assert str(error) == "MAIL_SOURCE_MISSING"
        else:
            raise AssertionError("malformed FETCH must fail closed")


def test_invalid_or_unknown_envelope_times_fail_closed() -> None:
    from risk_platform.mailbox.connection import _parse_internal_date, _parse_sent_at

    assert _parse_sent_at("Tue, 11 Aug 2026 10:00:00 -0000") is None
    assert _parse_sent_at("not-a-date") is None
    assert _parse_internal_date("31-Feb-2026 10:00:00 +0800") is None
    assert _parse_internal_date("11-Aug-2026 10:00:00") is None
    assert _parse_internal_date("1-Aug-2026 10:00:00 +0800") is None
    assert _parse_internal_date(" 11-Aug-2026 10:00:00 +0800") is None
    assert _parse_internal_date(" 1-Aug-2026 10:00:00 +0800") == datetime(
        2026, 8, 1, 2, tzinfo=UTC
    )
