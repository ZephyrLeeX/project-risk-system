from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

import pytest

from risk_platform.mailbox.connection import MailboxConnection
from risk_platform.mailbox.schemas import MailboxConfigRequest
from risk_platform.shared.outbound import OutboundEndpointGuard


class FakeImap:
    def __init__(self) -> None:
        self.selected: tuple[str, bool] | None = None
        self.logged_in = False
        self.closed = False

    def login(self, email: str, auth_code: str) -> tuple[str, list[bytes]]:
        assert email == "owner@example.com"
        assert auth_code == "secret-code"
        self.logged_in = True
        return "OK", [b"logged in"]

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected = (folder, readonly)
        return "OK", [b"0"]

    def logout(self) -> tuple[str, list[bytes]]:
        self.closed = True
        return "BYE", [b"logged out"]


async def fake_resolver(hostname: str, port: int) -> Sequence[str]:
    del hostname, port
    return ["93.184.216.34"]


def test_imap_test_logs_in_and_selects_folder_read_only() -> None:
    fake = FakeImap()
    connection = MailboxConnection(
        client_factory=lambda resolved, port, encryption, host: fake,
        outbound=OutboundEndpointGuard(
            resolver=cast("Callable[[str, int], Awaitable[Sequence[str]]]", fake_resolver)
        ),
    )

    outcome = asyncio.run(
        connection.test(
            email="owner@example.com",
            auth_code="secret-code",
            host="imap.example.com",
            port=993,
            encryption="SSL",
            folder="INBOX",
        )
    )

    assert outcome.success is True
    assert fake.selected == ("INBOX", True)
    assert fake.closed is True


def test_imap_test_classifies_auth_failure_without_secret() -> None:
    class BadImap(FakeImap):
        def login(self, email: str, auth_code: str) -> tuple[str, list[bytes]]:
            del email, auth_code
            raise RuntimeError("authentication failed for supplied credential")

    connection = MailboxConnection(
        client_factory=lambda resolved, port, encryption, host: BadImap(),
        outbound=OutboundEndpointGuard(resolver=fake_resolver),
    )
    outcome = asyncio.run(
        connection.test(
            email="owner@example.com",
            auth_code="do-not-echo",
            host="imap.example.com",
            port=993,
            encryption="SSL",
            folder="INBOX",
        )
    )

    assert outcome.error_code == "AUTHENTICATION_FAILED"
    assert "do-not-echo" not in (outcome.error_summary or "")


def test_mailbox_request_rejects_unknown_fields_and_normalizes_email() -> None:
    payload = MailboxConfigRequest(
        provider="QQ",
        email=" Owner@Example.com ",
        authCode="secret-code",
        imapHost="ignored.example.com",
        imapPort=1,
        encryption="STARTTLS",
        folder="INBOX",
        subjectKeywords=[" 项目周报 ", "项目周报"],
        initialSyncWeeks=4,
        readAttachments=True,
        aiExtractionEnabled=True,
    )
    assert payload.email == "owner@example.com"
    assert payload.subjectKeywords == ["项目周报"]
    with pytest.raises(ValueError):
        MailboxConfigRequest.model_validate({**payload.model_dump(), "secret": "never"})
