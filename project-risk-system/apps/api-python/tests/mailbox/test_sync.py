from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from risk_platform.mailbox.connection import MailboxConnection
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

    def uid(self, operation: str, *args: str) -> tuple[str, list[bytes]]:
        if operation == "search":
            return "OK", [b"7 8"]
        assert operation == "fetch"
        uid = args[0]
        return "OK", [
            b"Message-ID: <id-"
            + uid.encode()
            + b">\r\nSubject: Weekly status\r\nFrom: sender@example.com\r\n"
            + b"Date: Tue, 11 Aug 2026 10:00:00 +0000\r\n\r\n"
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
    assert not hasattr(snapshot.envelopes[0], "source")
