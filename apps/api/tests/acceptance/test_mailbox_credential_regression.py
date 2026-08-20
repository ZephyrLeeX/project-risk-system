from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select

from risk_platform.mailbox.connection import ConnectionOutcome, MailboxConnection, MailSyncSnapshot
from risk_platform.mailbox.models import MailboxConfig
from risk_platform.mailbox.schemas import MailboxConfigRequest
from risk_platform.mailbox.service import MailboxService
from risk_platform.mailbox.sync import MailboxSyncService

from .conftest import AcceptanceEnv, AcceptanceHarness


class RecordingMailboxConnection:
    def __init__(self) -> None:
        self.test_auth_codes: list[str] = []
        self.discover_auth_codes: list[str] = []

    async def test(self, **kwargs: object) -> ConnectionOutcome:
        self.test_auth_codes.append(cast(str, kwargs["auth_code"]))
        return ConnectionOutcome(success=True, latency_ms=1)

    async def discover(self, **kwargs: object) -> MailSyncSnapshot:
        self.discover_auth_codes.append(cast(str, kwargs["auth_code"]))
        return MailSyncSnapshot(uid_validity=1, envelopes=())


def _payload(auth_code: str | None, *, email: str = "owner@example.com") -> MailboxConfigRequest:
    return MailboxConfigRequest(
        provider="IMAP",
        email=email,
        authCode=auth_code,
        imapHost="imap.example.com",
        imapPort=993,
        encryption="SSL",
        folder="INBOX",
        subjectKeywords=["项目周报"],
        initialSyncWeeks=4,
        readAttachments=True,
        aiExtractionEnabled=True,
    )


def test_mailbox_save_persists_legacy_credential_for_test_and_discover(
    acceptance: AcceptanceHarness, acceptance_env: AcceptanceEnv
) -> None:
    async def scenario() -> None:
        connection = RecordingMailboxConnection()
        mailbox = MailboxService(
            acceptance_env.factory,
            acceptance_env.cipher,
            cast(MailboxConnection, connection),
        )
        identity = acceptance.identity_for("RISK_ADMIN")
        trace_id = UUID("00000000-0000-4000-8000-000000000052")

        await mailbox.save(_payload("secret-code"), identity, trace_id)
        async with acceptance_env.factory() as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == UUID(identity.user.id))
            )
            assert config is not None
            assert config.authCodeLast4 == "-code"
            assert "secret-code" not in config.encryptedAuthCode

        # The request intentionally omits authCode: this must decrypt the persisted value.
        await mailbox.test(_payload(None), identity, uuid4())
        sync = MailboxSyncService(
            acceptance_env.factory,
            acceptance_env.cipher,
            cast(MailboxConnection, connection),
        )
        async with acceptance_env.factory() as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == UUID(identity.user.id))
            )
            assert config is not None
        await sync._discover(config)
        assert connection.test_auth_codes == ["secret-code"]
        assert connection.discover_auth_codes == ["secret-code"]

        # Changing connection fields without authCode retains the old credential.
        await mailbox.save(_payload(None, email="renamed@example.com"), identity, uuid4())
        await mailbox.test(_payload(None, email="renamed@example.com"), identity, uuid4())
        assert connection.test_auth_codes[-1] == "secret-code"

        # Supplying a new authCode replaces the old ciphertext and last-four mask.
        async with acceptance_env.factory() as session:
            before = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == UUID(identity.user.id))
            )
            assert before is not None
            old_ciphertext = before.encryptedAuthCode
        await mailbox.save(_payload("new-secret"), identity, uuid4())
        async with acceptance_env.factory() as session:
            after = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == UUID(identity.user.id))
            )
            assert after is not None
            assert after.encryptedAuthCode != old_ciphertext
            assert after.authCodeLast4 == "cret"
            assert acceptance_env.cipher.decrypt_legacy(
                MailboxService._legacy_secret(after)
            ) == "new-secret"

    asyncio.run(scenario())
