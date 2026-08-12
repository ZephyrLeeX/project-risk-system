from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.app import AppComposition, create_app
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.mailbox.connection import MailEnvelope
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailboxEncryption,
    MailboxProvider,
    MailMessage,
    MailMessageProjectMatch,
    MailMessageStatus,
    MailProjectMatchType,
    MailReceivedAtSource,
    MailRiskCandidate,
    MailRiskCandidateStatus,
    MailSourceHandoff,
    MailSyncBatch,
    MailSyncTrigger,
)
from risk_platform.mailbox.models import ProjectRiskLevel as CandidateRiskLevel
from risk_platform.mailbox.sync import MailboxSyncService
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType
from risk_platform.reliability.models import DurableTask, DurableTaskKind
from risk_platform.risks.models import (
    ProjectRiskLevel,
    Risk,
    RiskCategory,
    RiskSourceType,
    RiskStatus,
)
from risk_platform.shared.crypto import KeyRing, SecretCipher
from risk_platform.shared.errors import ApiError
from risk_platform.todos.models import (
    ActionItem,
    ActionItemSourceType,
    ActionItemStatus,
    ActionItemUrgency,
)
from risk_platform.weekly_reports.api import get_weekly_report_service
from risk_platform.weekly_reports.api import router as weekly_router
from risk_platform.weekly_reports.models import WeeklyReportAggregate, WeeklyReportItem
from risk_platform.weekly_reports.service import (
    WeeklyReportService,
    invalidate_risk,
    shanghai_week_start,
)

ROOT = Path(__file__).resolve().parents[2]
USER_ID = UUID("00000000-0000-0000-0000-000000000027")
FIXED_NOW = datetime(2026, 8, 12, 15, tzinfo=UTC)
WEEK_START = date(2026, 8, 10)


@pytest.fixture(scope="module")
def weekly_database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL weekly-report validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t027_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()
    engine = create_database_engine(f"{sync_url}?options=-csearch_path%3D{schema}")
    factory = create_session_factory(engine)
    try:
        asyncio.run(_seed(factory))
        yield factory
    finally:
        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(factory) as session:
        user = User(
            id=USER_ID,
            username="t027-user",
            passwordHash="not-a-real-password-hash",
            displayName="T027",
        )
        category = RiskCategory(code="T027", name="T027 类别")
        owned = Project(name="范围内项目", managerId=USER_ID)
        outside = Project(name="范围外项目")
        session.add_all((user, category, owned, outside))
        await session.flush()
        mailbox = MailboxConfig(
            userId=user.id,
            provider=MailboxProvider.IMAP,
            email="owner@example.test",
            imapHost="imap.example.test",
            imapPort=993,
            encryption=MailboxEncryption.SSL,
            encryptedAuthCode="ciphertext",
            authCodeIv="iv",
            authCodeTag="tag",
            authCodeLast4="last",
            subjectKeywords=["周报"],
        )
        sync_task = DurableTask(
            kind=DurableTaskKind.MAILBOX_SYNC,
            idempotencyKey="t027-sync",
            payload={},
            maxAttempts=3,
        )
        parse_task = DurableTask(
            kind=DurableTaskKind.ATTACHMENT_PARSE,
            idempotencyKey="t027-parse",
            payload={},
            maxAttempts=3,
        )
        session.add_all((mailbox, sync_task, parse_task))
        await session.flush()
        batch = MailSyncBatch(
            taskId=sync_task.id,
            code="T027-BATCH",
            mailboxConfigId=mailbox.id,
            trigger=MailSyncTrigger.MANUAL,
        )
        session.add(batch)
        await session.flush()

        sent = datetime(2026, 8, 9, 16, tzinfo=UTC)
        received = datetime(2026, 8, 9, 15, 59, 59, tzinfo=UTC)
        owned_message = MailMessage(
            mailboxConfigId=mailbox.id,
            batchId=batch.id,
            messageId="<owned-weekly>",
            uidValidity=42,
            imapUid=27,
            subject="范围内项目周报",
            sentAt=sent,
            receivedAt=received,
            receivedAtSource=MailReceivedAtSource.IMAP_INTERNALDATE,
            status=MailMessageStatus.COMPLETED,
            sanitizedSummary="范围内项目周报摘要",
        )
        fallback_message = MailMessage(
            mailboxConfigId=mailbox.id,
            batchId=batch.id,
            messageId="<outside-weekly>",
            uidValidity=42,
            imapUid=28,
            subject="范围外项目周报",
            sentAt=None,
            receivedAt=datetime(2026, 8, 10, 1, tzinfo=UTC),
            receivedAtSource=MailReceivedAtSource.IMAP_INTERNALDATE,
            status=MailMessageStatus.COMPLETED,
            sanitizedSummary="范围外项目周报摘要",
        )
        handoff = MailSourceHandoff(
            mailboxConfigId=mailbox.id,
            batchId=batch.id,
            parseTaskId=parse_task.id,
            uidValidity=42,
            imapUid=27,
            messageId="<owned-weekly>",
            sentAt=sent,
            receivedAt=received,
            receivedAtSource=MailReceivedAtSource.IMAP_INTERNALDATE,
        )
        session.add_all((owned_message, fallback_message, handoff))
        await session.flush()
        session.add_all(
            (
                MailMessageProjectMatch(
                    messageId=owned_message.id,
                    projectId=owned.id,
                    matchType=MailProjectMatchType.EXACT,
                    confidence=100,
                    matchedText=owned.name,
                ),
                MailMessageProjectMatch(
                    messageId=fallback_message.id,
                    projectId=outside.id,
                    matchType=MailProjectMatchType.EXACT,
                    confidence=100,
                    matchedText=outside.name,
                ),
            )
        )
        for index, (project, message) in enumerate(
            ((owned, owned_message), (outside, fallback_message)), 1
        ):
            risk = Risk(
                projectId=project.id,
                categoryId=category.id,
                title=f"风险 {index}",
                description=f"风险描述 {index}",
                level=ProjectRiskLevel.HIGH if index == 1 else ProjectRiskLevel.MEDIUM,
                status=RiskStatus.ACTIVE,
                sourceType=RiskSourceType.MAIL_AI,
                dedupeFingerprint=f"t027-{index}",
            )
            session.add(risk)
            await session.flush()
            candidate = MailRiskCandidate(
                messageId=message.id,
                projectId=project.id,
                categoryId=category.id,
                level=CandidateRiskLevel.HIGH if index == 1 else CandidateRiskLevel.MEDIUM,
                description=f"候选 {index}",
                evidence=f"证据 {index}",
                suggestion=f"建议 {index}",
                confidence=90,
                status=MailRiskCandidateStatus.CONFIRMED,
                confirmedRiskId=risk.id,
            )
            todo = ActionItem(
                riskId=risk.id,
                projectId=project.id,
                title=f"待办 {index}",
                description=f"待办描述 {index}",
                urgency=ActionItemUrgency.HIGH,
                status=ActionItemStatus.PENDING,
                sourceType=ActionItemSourceType.RISK_SUGGESTION,
            )
            session.add_all((candidate, todo))

    service = WeeklyReportService(factory, clock=lambda: FIXED_NOW)
    async with factory() as session:
        project_ids = (
            await session.scalars(select(Project.id).order_by(Project.name.desc()))
        ).all()
    for project_id in project_ids:
        await service.rebuild(WEEK_START, project_id, 1)


def _identity(
    scope: DataScopeType = DataScopeType.OWNED,
    *,
    permissions: list[str] | None = None,
) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=FIXED_NOW + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(USER_ID),
            username="t027",
            displayName="T027",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=permissions if permissions is not None else ["dashboard.view"],
            dataScope=scope.value,
            mustChangePassword=False,
        ),
    )


async def _client(
    factory: async_sessionmaker[AsyncSession], identity: SessionIdentity
) -> httpx2.AsyncClient:
    service = WeeklyReportService(factory, clock=lambda: FIXED_NOW)

    async def override_identity() -> SessionIdentity:
        return identity

    app = create_app(
        Settings(environment="test"),
        AppComposition(
            routers=(weekly_router,),
            dependency_overrides={
                current_identity: override_identity,
                get_weekly_report_service: lambda: service,
            },
        ),
    )
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
    )


def test_shanghai_boundary_is_dst_neutral_and_uses_received_fallback() -> None:
    assert shanghai_week_start(datetime(2026, 8, 9, 15, 59, 59, tzinfo=UTC)) == date(
        2026, 8, 3
    )
    assert shanghai_week_start(datetime(2026, 8, 9, 16, tzinfo=UTC)) == WEEK_START
    assert shanghai_week_start(datetime(2026, 8, 10, 1, tzinfo=UTC)) == WEEK_START


def test_alembic_backfills_frozen_times_from_pre_t027_facts() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; T027 migration backfill 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t027_backfill_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    user_id, mailbox_id, task_id, parse_task_id, batch_id, message_id, handoff_id = (
        uuid.uuid4() for _ in range(7)
    )
    observed = datetime(2026, 8, 1, 1, 2, 3, 456000, tzinfo=UTC)
    try:
        with engine.connect() as connection:
            config = Config(ROOT / "alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "20260812_0006")
            connection.execute(
                text(
                    'INSERT INTO users (id, username, "passwordHash", "displayName", "updatedAt") '
                    "VALUES (:id, 'legacy', 'hash', 'Legacy', :observed)"
                ),
                {"id": user_id, "observed": observed},
            )
            connection.execute(
                text(
                    'INSERT INTO mailbox_configs (id, "userId", provider, email, "imapHost", '
                    '"imapPort", encryption, "encryptedAuthCode", "authCodeIv", "authCodeTag", '
                    '"authCodeLast4", "subjectKeywords", "updatedAt") VALUES '
                    "(:id, :user_id, 'IMAP', 'legacy@example.test', 'imap.example.test', 993, "
                    "'SSL', 'cipher', 'iv', 'tag', 'last', '[\"周报\"]'::jsonb, :observed)"
                ),
                {"id": mailbox_id, "user_id": user_id, "observed": observed},
            )
            for current_id, kind, key in (
                (task_id, "MAILBOX_SYNC", "legacy-sync"),
                (parse_task_id, "ATTACHMENT_PARSE", "legacy-parse"),
            ):
                connection.execute(
                    text(
                        'INSERT INTO durable_tasks (id, kind, "idempotencyKey", "maxAttempts", '
                        '"updatedAt") VALUES (:id, CAST(:kind AS "DurableTaskKind"), :key, 3, '
                        ":observed)"
                    ),
                    {"id": current_id, "kind": kind, "key": key, "observed": observed},
                )
            connection.execute(
                text(
                    'INSERT INTO mail_sync_batches (id, "mailboxConfigId", "taskId", code, '
                    'trigger, "updatedAt") VALUES '
                    "(:id, :mailbox_id, :task_id, 'LEGACY', 'MANUAL', :observed)"
                ),
                {
                    "id": batch_id,
                    "mailbox_id": mailbox_id,
                    "task_id": task_id,
                    "observed": observed,
                },
            )
            connection.execute(
                text(
                    'INSERT INTO mail_messages (id, "mailboxConfigId", "batchId", "messageId", '
                    '"imapUid", subject, "sentAt", "createdAt", "updatedAt") VALUES '
                    "(:id, :mailbox_id, :batch_id, '<legacy>', 7, 'legacy', :sent, :observed, "
                    ":observed)"
                ),
                {
                    "id": message_id,
                    "mailbox_id": mailbox_id,
                    "batch_id": batch_id,
                    "sent": datetime(2026, 7, 31, 23, tzinfo=UTC),
                    "observed": observed,
                },
            )
            connection.execute(
                text(
                    'INSERT INTO mail_source_handoffs (id, "mailboxConfigId", "batchId", '
                    '"parseTaskId", "uidValidity", "imapUid", "messageId", "envelopeMetadata", '
                    '"createdAt", "updatedAt") VALUES (:id, :mailbox_id, :batch_id, '
                    ":parse_task_id, 42, 7, '<legacy>', CAST(:metadata AS jsonb), :observed, "
                    ":observed)"
                ),
                {
                    "id": handoff_id,
                    "mailbox_id": mailbox_id,
                    "batch_id": batch_id,
                    "parse_task_id": parse_task_id,
                    "metadata": '{"sent_at":"2026-07-31T23:00:00+00:00"}',
                    "observed": observed,
                },
            )
            connection.commit()
            command.upgrade(config, "head")
            connection.commit()
            handoff = connection.execute(
                text(
                    'SELECT "sentAt", "receivedAt", "receivedAtSource" '
                    "FROM mail_source_handoffs WHERE id = :id"
                ),
                {"id": handoff_id},
            ).one()
            message = connection.execute(
                text(
                    'SELECT "uidValidity", "sentAt", "receivedAt", "receivedAtSource" '
                    "FROM mail_messages WHERE id = :id"
                ),
                {"id": message_id},
            ).one()
            assert handoff.sentAt is None
            assert handoff.receivedAt == observed
            assert handoff.receivedAtSource == "FIRST_DURABLE_OBSERVATION"
            assert message.uidValidity == 42
            assert message.sentAt is None
            assert message.receivedAt == observed
            assert message.receivedAtSource == "FIRST_DURABLE_OBSERVATION"
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_rebuild_is_idempotent_and_uses_sent_then_received(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        service = WeeklyReportService(weekly_database, clock=lambda: FIXED_NOW)
        async with weekly_database() as session:
            owned = await session.scalar(select(Project).where(Project.name == "范围内项目"))
            assert owned is not None
        await service.rebuild(WEEK_START, owned.id, 1)
        async with weekly_database() as session:
            aggregate = await session.scalar(
                select(WeeklyReportAggregate).where(
                    WeeklyReportAggregate.projectId == owned.id
                )
            )
            assert aggregate is not None
            items = (
                await session.scalars(
                    select(WeeklyReportItem).where(
                        WeeklyReportItem.aggregateId == aggregate.id
                    )
                )
            ).all()
            assert aggregate.sourceRevision == 1
            assert aggregate.riskCount == 1
            assert len(items) == 1
            assert items[0].occurredAt == datetime(2026, 8, 9, 16, tzinfo=UTC)

    asyncio.run(scenario())


def test_api_reapplies_project_scope_and_returns_minimized_detail(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        client = await _client(weekly_database, _identity())
        try:
            response = await client.get("/api/weekly-reports/current")
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["weekStart"] == "2026-08-10"
            assert data["weekEnd"] == "2026-08-17"
            assert [item["project"]["name"] for item in data["projects"]] == ["范围内项目"]
            assert data["summary"]["riskCount"] == 1
            project_id = data["projects"][0]["project"]["id"]
            detail = await client.get(
                f"/api/weekly-reports/2026-08-10/projects/{project_id}"
            )
            assert detail.status_code == 200
            item = detail.json()["data"]["items"][0]
            assert item["summary"] == "范围内项目周报摘要"
            assert "evidence" not in item
            async with weekly_database() as session:
                outside_id = await session.scalar(
                    select(Project.id).where(Project.name == "范围外项目")
                )
            outside = await client.get(
                f"/api/weekly-reports/2026-08-10/projects/{outside_id}"
            )
            assert outside.status_code == 404
            assert outside.json()["code"] == "PROJECT_NOT_FOUND"
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_api_requires_dashboard_permission_and_monday_week_start(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        forbidden_client = await _client(weekly_database, _identity(permissions=[]))
        allowed_client = await _client(weekly_database, _identity())
        try:
            forbidden = await forbidden_client.get("/api/weekly-reports/current")
            assert forbidden.status_code == 403
            assert forbidden.json()["code"] == "FORBIDDEN"
            invalid = await allowed_client.get("/api/weekly-reports/2026-08-11")
            assert invalid.status_code == 422
            assert invalid.json()["code"] == "VALIDATION_ERROR"
        finally:
            await forbidden_client.aclose()
            await allowed_client.aclose()

    asyncio.run(scenario())


def test_stale_and_empty_states_are_explicit(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        late = WeeklyReportService(weekly_database, clock=lambda: FIXED_NOW + timedelta(minutes=16))
        with pytest.raises(ApiError) as caught:
            await late.report(_identity(), WEEK_START)
        assert getattr(caught.value, "code", None) == "WEEKLY_REPORT_STALE"
        service = WeeklyReportService(weekly_database, clock=lambda: FIXED_NOW)
        with pytest.raises(ApiError) as missing:
            await service.report(_identity(), date(2026, 8, 3))
        assert getattr(missing.value, "code", None) == "WEEKLY_REPORT_STALE"
        assert missing.value.data == {
            "weekStart": "2026-08-03",
            "projectId": None,
            "retryAfterSeconds": 60,
        }

    asyncio.run(scenario())


def test_reconciliation_enqueues_expired_materializations_once_per_project(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        service = WeeklyReportService(weekly_database, clock=lambda: FIXED_NOW)
        assert await service.reconcile(FIXED_NOW) == 0
        assert await service.reconcile(FIXED_NOW + timedelta(minutes=16)) == 2
        async with weekly_database() as session:
            tasks = (
                await session.scalars(
                    select(DurableTask).where(
                        DurableTask.kind == DurableTaskKind.WEEKLY_REPORT_REBUILD,
                        DurableTask.idempotencyKey.like("%:2"),
                    )
                )
            ).all()
            assert len(tasks) == 2

    asyncio.run(scenario())


def test_late_business_update_invalidates_and_rebuilds_next_revision(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        service = WeeklyReportService(weekly_database, clock=lambda: FIXED_NOW)
        async with transaction(weekly_database) as session:
            risk = await session.scalar(select(Risk).where(Risk.title == "风险 1"))
            assert risk is not None
            risk.status = RiskStatus.RESOLVED
            todo = await session.scalar(select(ActionItem).where(ActionItem.riskId == risk.id))
            assert todo is not None
            todo.status = ActionItemStatus.COMPLETED
            await invalidate_risk(session, risk.id)
        async with weekly_database() as session:
            aggregate = await session.scalar(
                select(WeeklyReportAggregate).join(
                    Project, Project.id == WeeklyReportAggregate.projectId
                ).where(Project.name == "范围内项目")
            )
            assert aggregate is not None
            task = await session.scalar(
                select(DurableTask).where(
                    DurableTask.kind == DurableTaskKind.WEEKLY_REPORT_REBUILD,
                    DurableTask.idempotencyKey
                    == f"weekly-report:{WEEK_START.isoformat()}:{aggregate.projectId}:2",
                )
            )
            assert task is not None and aggregate is not None and aggregate.stale
            payload = task.payload
        await service.handle(payload)
        async with weekly_database() as session:
            aggregate = await session.scalar(
                select(WeeklyReportAggregate).join(
                    Project, Project.id == WeeklyReportAggregate.projectId
                ).where(Project.name == "范围内项目")
            )
            assert aggregate is not None
            item = await session.scalar(
                select(WeeklyReportItem).where(WeeklyReportItem.aggregateId == aggregate.id)
            )
            assert aggregate.sourceRevision == 2 and not aggregate.stale
            assert item is not None
            assert item.riskStatus is RiskStatus.RESOLVED
            assert item.todoStatus is ActionItemStatus.COMPLETED

    asyncio.run(scenario())


def test_reconciliation_detects_authority_row_removal(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        service = WeeklyReportService(weekly_database, clock=lambda: FIXED_NOW)
        async with transaction(weekly_database) as session:
            project = await session.scalar(select(Project).where(Project.name == "范围内项目"))
            message = await session.scalar(
                select(MailMessage).where(MailMessage.uidValidity == 42, MailMessage.imapUid == 27)
            )
            assert project is not None and message is not None
            match = await session.scalar(
                select(MailMessageProjectMatch).where(
                    MailMessageProjectMatch.messageId == message.id,
                    MailMessageProjectMatch.projectId == project.id,
                )
            )
            assert match is not None
            await session.delete(match)
        assert await service.reconcile(FIXED_NOW) >= 1
        async with weekly_database() as session:
            aggregate = await session.scalar(
                select(WeeklyReportAggregate).where(
                    WeeklyReportAggregate.projectId == project.id,
                    WeeklyReportAggregate.weekStart == WEEK_START,
                )
            )
            assert aggregate is not None and aggregate.stale

    asyncio.run(scenario())


def test_fallback_is_first_transaction_timestamp_and_retry_does_not_recompute(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        cipher = SecretCipher(KeyRing(active_version="v1", keys={"v1": b"x" * 32}))
        service = MailboxSyncService(weekly_database, cipher)
        async with transaction(weekly_database) as session:
            mailbox = await session.scalar(select(MailboxConfig))
            batch = await session.scalar(select(MailSyncBatch))
            assert mailbox is not None and batch is not None
            envelope = MailEnvelope(
                uid=999,
                uid_validity=42,
                message_id="<fallback>",
                subject="fallback",
                sender="sender@example.test",
                sent_at=None,
                received_at=None,
            )
            await service._handoff(session, batch, mailbox, envelope)
        async with weekly_database() as session:
            handoff = await session.scalar(
                select(MailSourceHandoff).where(MailSourceHandoff.imapUid == 999)
            )
            assert handoff is not None
            frozen = handoff.receivedAt
            assert handoff.receivedAtSource is MailReceivedAtSource.FIRST_DURABLE_OBSERVATION
            assert abs(handoff.receivedAt - handoff.createdAt) <= timedelta(milliseconds=1)
        async with transaction(weekly_database) as session:
            mailbox = await session.scalar(select(MailboxConfig))
            batch = await session.scalar(select(MailSyncBatch))
            assert mailbox is not None and batch is not None
            await service._handoff(
                session,
                batch,
                mailbox,
                MailEnvelope(
                    uid=999,
                    uid_validity=42,
                    message_id="<fallback>",
                    subject="fallback",
                    sender="sender@example.test",
                    sent_at=datetime(2030, 1, 1, tzinfo=UTC),
                    received_at=datetime(2030, 1, 1, tzinfo=UTC),
                ),
            )
        async with weekly_database() as session:
            handoff = await session.scalar(
                select(MailSourceHandoff).where(MailSourceHandoff.imapUid == 999)
            )
            assert handoff is not None and handoff.receivedAt == frozen

    asyncio.run(scenario())


def test_postgresql_triggers_reject_envelope_time_mutation(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        async with weekly_database() as session:
            targets = (
                (
                    "mail_messages",
                    await session.scalar(select(MailMessage.id).where(MailMessage.imapUid == 27)),
                ),
                (
                    "mail_source_handoffs",
                    await session.scalar(
                        select(MailSourceHandoff.id).where(MailSourceHandoff.imapUid == 27)
                    ),
                ),
            )
        for table_name, target_id in targets:
            assert target_id is not None
            async with weekly_database() as session:
                with pytest.raises(DBAPIError, match="mail envelope time facts are immutable"):
                    await session.execute(
                        text(
                            f'UPDATE {table_name} SET "receivedAt" = '
                            '"receivedAt" + interval \'1 second\' WHERE id = :id'
                        ),
                        {"id": target_id},
                    )
                    await session.commit()
                await session.rollback()

    asyncio.run(scenario())


def test_uidvalidity_reuse_creates_an_independent_message_identity(
    weekly_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        async with weekly_database() as session, session.begin():
            original = await session.scalar(
                select(MailMessage).where(
                    MailMessage.uidValidity == 42, MailMessage.imapUid == 27
                )
            )
            assert original is not None
            frozen = original.receivedAt
            replacement = MailMessage(
                mailboxConfigId=original.mailboxConfigId,
                batchId=original.batchId,
                messageId="<uid-reused-after-reset>",
                uidValidity=43,
                imapUid=27,
                subject="new mailbox epoch",
                receivedAt=datetime(2026, 8, 12, 12, tzinfo=UTC),
                receivedAtSource=MailReceivedAtSource.IMAP_INTERNALDATE,
            )
            session.add(replacement)
        async with weekly_database() as session:
            messages = (
                await session.scalars(
                    select(MailMessage)
                    .where(MailMessage.imapUid == 27)
                    .order_by(MailMessage.uidValidity)
                )
            ).all()
            assert [message.uidValidity for message in messages] == [42, 43]
            assert messages[0].receivedAt == frozen

    asyncio.run(scenario())
