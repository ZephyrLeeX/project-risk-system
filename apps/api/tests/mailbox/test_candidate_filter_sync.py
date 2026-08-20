"""Focused PostgreSQL integration test for the MailCandidateFilter sync flow.

Exercises ``MailboxSyncService.run`` against an isolated Alembic-created
PostgreSQL schema with a recording IMAP connection that returns a mixed set of
weekly-report and non-weekly envelopes. Asserts the deterministic filter and
scanned-cursor contract:

* only weekly-report candidates create a handoff + durable parse task;
* non-weekly mail never enqueues a parse task (no body fetch);
* the scanned cursor advances to the highest *scanned* UID, not the highest
  accepted UID, so skipped non-weekly mail is never re-scanned on the next
  sync;
* UIDVALIDITY reset still fails the batch and clears the cursor.

Skipped without ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Coroutine, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.mailbox.connection import (
    ConnectionOutcome,
    MailboxConnection,
    MailEnvelope,
    MailSyncSnapshot,
)
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailboxEncryption,
    MailboxProvider,
    MailSourceHandoff,
    MailSyncBatch,
    MailSyncStatus,
    MailSyncTrigger,
)
from risk_platform.mailbox.sync import MailboxSyncService
from risk_platform.reliability.models import DurableTask, DurableTaskKind
from risk_platform.shared.crypto import SecretCipher

ROOT = Path(__file__).resolve().parents[2]


class _StubCipher:
    def decrypt_legacy(self, fields: object) -> str:
        del fields
        return "unused"


class RecordingConnection:
    """Returns a fixed, ordered mix of weekly + non-weekly envelopes."""

    def __init__(self, envelopes: tuple[MailEnvelope, ...], uid_validity: int = 42) -> None:
        self._envelopes = envelopes
        self._uid_validity = uid_validity
        self.discover_calls = 0

    async def test(self, **kwargs: object) -> ConnectionOutcome:
        return ConnectionOutcome(success=True, latency_ms=1)

    async def discover(self, **kwargs: object) -> MailSyncSnapshot:
        self.discover_calls += 1
        return MailSyncSnapshot(uid_validity=self._uid_validity, envelopes=self._envelopes)

    async def fetch_source(self, **kwargs: object) -> bytes:
        raise AssertionError("non-candidate mail must never fetch its body")


def _envelope(uid: int, subject: str, *, sender: str = "sender@example.com") -> MailEnvelope:
    return MailEnvelope(
        uid=uid,
        uid_validity=42,
        message_id=f"<id-{uid}@test>",
        subject=subject,
        sender=sender,
        sent_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
        received_at=datetime(2026, 8, 11, 10, 30, tzinfo=UTC),
    )


@pytest.fixture
def mailbox_postgresql() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; mailbox candidate-filter PG validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"mcf_{uuid.uuid4().hex}"
    admin = create_engine(sync_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with migration.connect() as connection:
            config = Config(ROOT / "alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        engine = create_database_engine(
            f"{sync_url}?options=-csearch_path%3D{schema}", pool_pre_ping=False
        )
        try:
            yield create_session_factory(engine)
        finally:
            asyncio.run(engine.dispose())
    finally:
        migration.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict[str, object]:
    suffix = uuid.uuid4().hex
    async with transaction(factory) as session:
        department = Department(code=f"MCF-{suffix}", name="MCF")
        owner = User(username=f"owner-{suffix}", passwordHash="not-used", displayName="Owner")
        session.add_all((department, owner))
        await session.flush()
        owner.departmentId = department.id
        batch_task = DurableTask(
            kind=DurableTaskKind.MAILBOX_SYNC,
            idempotencyKey=f"batch-{suffix}",
            payload={},
            maxAttempts=1,
        )
        mailbox = MailboxConfig(
            userId=owner.id,
            provider=MailboxProvider.IMAP,
            email="owner@example.com",
            imapHost="imap.example.test",
            imapPort=993,
            encryption=MailboxEncryption.SSL,
            encryptedAuthCode="unused",
            authCodeIv="unused",
            authCodeTag="unused",
            authCodeLast4="used",
            subjectKeywords=["周报", "项目周报", "工作周报", "项目工作周报"],
        )
        session.add_all((mailbox, batch_task))
        await session.flush()
        batch = MailSyncBatch(
            taskId=batch_task.id,
            code=f"B-{suffix}",
            mailboxConfigId=mailbox.id,
            trigger=MailSyncTrigger.MANUAL,
        )
        session.add(batch)
        await session.flush()
        return {"mailbox": mailbox.id, "batch": batch.id, "owner": owner.id}


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def test_sync_filters_non_weekly_mail_and_advances_cursor_past_skipped_uids(
    mailbox_postgresql: async_sessionmaker[AsyncSession],
) -> None:
    factory = mailbox_postgresql

    async def scenario() -> None:
        seed = await _seed(factory)
        # 10 envelopes: only 3 are weekly reports (UIDs 3, 7, 9). The rest are
        # AD-expiry alerts / verification codes / system notifications.
        envelopes = tuple(
            _envelope(uid, subject)
            for uid, subject in (
                (1, "AD密码修改到期提醒"),
                (2, "验证码"),
                (3, "XX项目周报"),
                (4, "系统通知"),
                (5, "AD密码修改到期提醒"),
                (6, "系统通知"),
                (7, "【周报】海外交付项目"),
                (8, "验证码"),
                (9, "XX项目工作周报-2026W34"),
                (10, "AD密码修改到期提醒"),
            )
        )
        connection = RecordingConnection(envelopes)
        service = MailboxSyncService(
            factory, cast("SecretCipher", _StubCipher()), cast("MailboxConnection", connection)
        )
        await service.run(cast("uuid.UUID", seed["batch"]))

        async with factory() as session:
            config = await session.get(MailboxConfig, cast("uuid.UUID", seed["mailbox"]))
            assert config is not None
            # Scanned cursor advances to the highest *scanned* UID (10), not the
            # highest accepted weekly-report UID (9).
            assert config.uidCursor == 10
            handoffs = (
                await session.scalars(
                    select(MailSourceHandoff).where(
                        MailSourceHandoff.mailboxConfigId == config.id
                    )
                )
            ).all()
            # Only the 3 weekly-report candidates created a handoff.
            assert sorted(row.imapUid for row in handoffs) == [3, 7, 9]
            batch = await session.get(MailSyncBatch, cast("uuid.UUID", seed["batch"]))
            assert batch is not None
            assert batch.discoveredCount == 10
            assert batch.skippedCount == 7
            assert batch.handedOffCount == 3
            assert batch.endUid == 10
            assert batch.startUid == 1
            assert batch.cursorAdvanced is True
            assert batch.status == MailSyncStatus.SUCCESS

    _run(scenario())


def test_sync_does_not_re_scan_skipped_uids_on_next_round(
    mailbox_postgresql: async_sessionmaker[AsyncSession],
) -> None:
    factory = mailbox_postgresql

    async def scenario() -> None:
        seed = await _seed(factory)
        connection = RecordingConnection(
            tuple(
                _envelope(uid, subject)
                for uid, subject in (
                    (1, "AD密码修改到期提醒"),
                    (2, "项目周报"),
                    (3, "验证码"),
                )
            )
        )
        service = MailboxSyncService(
            factory, cast("SecretCipher", _StubCipher()), cast("MailboxConnection", connection)
        )
        await service.run(cast("uuid.UUID", seed["batch"]))
        assert connection.discover_calls == 1
        # A second batch over the same config: the discover cursor is now 3, so
        # the IMAP criterion is ``UID 4:*``. With nothing new, no handoffs are
        # created and the cursor stays advanced — skipped mail is not rescanned.
        async with transaction(factory) as session:
            task = DurableTask(
                kind=DurableTaskKind.MAILBOX_SYNC,
                idempotencyKey=f"batch2-{uuid.uuid4().hex}",
                payload={},
                maxAttempts=1,
            )
            session.add(task)
            await session.flush()
            batch2 = MailSyncBatch(
                taskId=task.id,
                code=f"B2-{uuid.uuid4().hex}",
                mailboxConfigId=cast("uuid.UUID", seed["mailbox"]),
                trigger=MailSyncTrigger.SCHEDULED,
            )
            session.add(batch2)
            await session.flush()
            batch2_id = batch2.id
        # Second round discovers nothing past the cursor.
        connection._envelopes = ()
        await service.run(batch2_id)
        assert connection.discover_calls == 2
        async with factory() as session:
            config = await session.get(MailboxConfig, cast("uuid.UUID", seed["mailbox"]))
            assert config is not None
            assert config.uidCursor == 3
            loaded_batch2 = await session.get(MailSyncBatch, batch2_id)
            assert loaded_batch2 is not None
            assert loaded_batch2.handedOffCount == 0
            assert loaded_batch2.status == MailSyncStatus.SUCCESS

    _run(scenario())


def test_sync_uidvalidity_reset_fails_batch_and_clears_cursor(
    mailbox_postgresql: async_sessionmaker[AsyncSession],
) -> None:
    factory = mailbox_postgresql

    async def scenario() -> None:
        seed = await _seed(factory)
        # Pre-establish a baseline cursor so the reset is observable.
        async with transaction(factory) as session:
            config = await session.get(
                MailboxConfig, cast("uuid.UUID", seed["mailbox"]), with_for_update=True
            )
            assert config is not None
            config.uidValidity = 42
            config.uidCursor = 100
        # The IMAP folder's UIDVALIDITY changed to 99.
        connection = RecordingConnection(
            (_envelope(101, "项目周报"),), uid_validity=99
        )
        service = MailboxSyncService(
            factory, cast("SecretCipher", _StubCipher()), cast("MailboxConnection", connection)
        )
        await service.run(cast("uuid.UUID", seed["batch"]))
        async with factory() as session:
            config = await session.get(MailboxConfig, cast("uuid.UUID", seed["mailbox"]))
            assert config is not None
            assert config.uidValidity == 99
            assert config.uidCursor is None
            batch = await session.get(MailSyncBatch, cast("uuid.UUID", seed["batch"]))
            assert batch is not None
            assert batch.status == MailSyncStatus.FAILURE

    _run(scenario())


def test_weekly_report_only_false_accepts_all_envelopes(
    mailbox_postgresql: async_sessionmaker[AsyncSession],
) -> None:
    factory = mailbox_postgresql

    async def scenario() -> None:
        seed = await _seed(factory)
        async with transaction(factory) as session:
            config = await session.get(
                MailboxConfig, cast("uuid.UUID", seed["mailbox"]), with_for_update=True
            )
            assert config is not None
            config.weeklyReportOnly = False
        connection = RecordingConnection(
            tuple(
                _envelope(uid, subject)
                for uid, subject in ((1, "AD密码修改到期提醒"), (2, "验证码"), (3, "项目周报"))
            )
        )
        service = MailboxSyncService(
            factory, cast("SecretCipher", _StubCipher()), cast("MailboxConnection", connection)
        )
        await service.run(cast("uuid.UUID", seed["batch"]))
        async with factory() as session:
            config = await session.get(MailboxConfig, cast("uuid.UUID", seed["mailbox"]))
            assert config is not None
            handoffs = (
                await session.scalars(
                    select(MailSourceHandoff).where(
                        MailSourceHandoff.mailboxConfigId == config.id
                    )
                )
            ).all()
            assert sorted(row.imapUid for row in handoffs) == [1, 2, 3]
            assert config.uidCursor == 3

    _run(scenario())


def test_keyword_change_changes_acceptance_and_hides_historical_non_weekly(
    mailbox_postgresql: async_sessionmaker[AsyncSession],
) -> None:
    factory = mailbox_postgresql

    async def scenario() -> None:
        seed = await _seed(factory)
        # First sync accepts the weekly report; "验证码" is skipped.
        connection = RecordingConnection(
            (_envelope(1, "验证码"), _envelope(2, "项目周报"))
        )
        service = MailboxSyncService(
            factory, cast("SecretCipher", _StubCipher()), cast("MailboxConnection", connection)
        )
        await service.run(cast("uuid.UUID", seed["batch"]))
        async with factory() as session:
            config = await session.get(MailboxConfig, cast("uuid.UUID", seed["mailbox"]))
            assert config is not None
            assert config.uidCursor == 2
        # Operator drops "周报" from the keyword set — non-weekly behaviour.
        async with transaction(factory) as session:
            config = await session.get(
                MailboxConfig, cast("uuid.UUID", seed["mailbox"]), with_for_update=True
            )
            assert config is not None
            config.subjectKeywords = ["里程碑"]
        connection._envelopes = (_envelope(3, "项目里程碑汇报"),)
        await service.run(cast("uuid.UUID", seed["batch"]))
        async with factory() as session:
            config = await session.get(MailboxConfig, cast("uuid.UUID", seed["mailbox"]))
            assert config is not None
            handoffs = (
                await session.scalars(
                    select(MailSourceHandoff).where(
                        MailSourceHandoff.mailboxConfigId == config.id
                    )
                )
            ).all()
            assert sorted(row.imapUid for row in handoffs) == [2, 3]

    _run(scenario())
