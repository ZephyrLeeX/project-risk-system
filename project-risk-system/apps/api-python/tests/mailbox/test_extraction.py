from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from celery import Celery
from celery.contrib.testing.worker import start_worker  # type: ignore[import-untyped]
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User
from risk_platform.ai_providers.client import AiProviderClient, ProviderRequestError
from risk_platform.ai_providers.models import AiConnectionStatus, AiProviderConfig
from risk_platform.audit.models import AuditActorType, AuditLog
from risk_platform.audit.service import AuditService
from risk_platform.auth.schemas import AuthenticatedUser, DataScope
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.mailbox.entrypoint import routers
from risk_platform.mailbox.extraction import (
    MailRiskCandidateService,
    MailRiskExtractionWorker,
    _ai_extraction_disabled,
    _CategoryOption,
    _parse_output,
    _provider_payload,
)
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailboxEncryption,
    MailboxProvider,
    MailMessage,
    MailMessageProjectMatch,
    MailProjectMatchType,
    MailReceivedAtSource,
    MailRiskCandidate,
    MailRiskCandidateStatus,
    MailSourceHandoff,
    MailStageStatus,
    MailSyncBatch,
    MailSyncTrigger,
)
from risk_platform.mailbox.parse_worker import MailParseWorker
from risk_platform.mailbox.schemas import MailRiskCandidateUpdateRequest
from risk_platform.projects.models import Project
from risk_platform.reliability.celery_app import create_celery_app
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.dispatcher import publish_outbox, register_executor
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.risks.models import ProjectRiskLevel, Risk, RiskCategory
from risk_platform.shared.crypto import SecretCipher
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]


def _options() -> list[_CategoryOption]:
    return [
        _CategoryOption(
            option_id="C1",
            category_id=UUID("00000000-0000-0000-0000-000000000001"),
            name="进度",
            description=None,
            default_level="MEDIUM",
        )
    ]


def test_provider_payload_is_v2_bounded_and_redacted() -> None:
    body = (
        "owner@example.com https://internal.example/a?token=x Authorization: Bearer secret 1234567"
    )
    payload = _provider_payload(
        body,
        ["attachment@example.com"],
        "2026-08-12",
        _options(),
    )

    assert payload["schema_version"] == "MAIL_PROVIDER_DERIVED_CONTENT_V2"
    assert payload["analysis_text"] == "BODY\n[EMAIL] [URL]\nATTACHMENT_1\n[EMAIL]"
    assert payload["content_stats"] == {
        "body_chars": len(body),
        "attachment_count": 1,
        "total_chars": len(str(payload["analysis_text"])),
        "redaction_count": 4,
        "truncated": False,
    }
    assert payload["risk_category_options"] == [
        {"option_id": "C1", "name": "进度", "description": None, "default_level": "MEDIUM"}
    ]


@pytest.mark.parametrize(
    "output",
    [
        '{"risks":[{"project_option_id":"P1","category_option_id":"C2","level":"HIGH","description":"风险描述","evidence":"证据","suggestion":"建议","confidence":80}]}',
        '{"risks":[{"project_option_id":"P1","category_option_id":["C1"],"level":"HIGH","description":"风险描述","evidence":"证据","suggestion":"建议","confidence":80}]}',
        '{"risks":[{"project_option_id":"P1","category_option_id":"C1","category":"free","level":"HIGH","description":"风险描述","evidence":"证据","suggestion":"建议","confidence":80}]}',
    ],
)
def test_provider_category_mapping_fails_closed(output: str) -> None:
    with pytest.raises(ValueError, match="PROVIDER_INVALID_OUTPUT"):
        _parse_output(
            output,
            {"P1": UUID("00000000-0000-0000-0000-000000000010")},
            {"C1": UUID("00000000-0000-0000-0000-000000000001")},
        )


def test_disabled_mailbox_skips_ai_extraction_without_provider_work() -> None:
    class DisabledMailbox:
        aiExtractionEnabled = False

    assert _ai_extraction_disabled(cast("MailboxConfig", DisabledMailbox())) is True
    assert _ai_extraction_disabled(None) is True

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def scalar(self, statement: object) -> object:
            del statement
            return None

        async def get(self, model: object, identity: object) -> MailboxConfig:
            del model, identity
            return cast("MailboxConfig", DisabledMailbox())

    session = Session()
    worker = MailRiskExtractionWorker(
        cast("async_sessionmaker[AsyncSession]", lambda: session),
        cast("SecretCipher", object()),
    )
    stage = AsyncMock()
    worker._stage = stage  # type: ignore[method-assign]

    asyncio.run(
        worker._extract(
            UUID("00000000-0000-0000-0000-000000000010"),
            1,
            2,
            "body",
            [],
            "2026-08-12",
        )
    )

    stage.assert_awaited_once()
    call = stage.await_args
    assert call is not None
    assert call.args[3:] == (MailStageStatus.SUCCEEDED, "AI_EXTRACTION_DISABLED")


def test_completed_handoff_skips_duplicate_delivery_before_provider_call() -> None:
    class CompletedHandoff:
        aiReviewStatus = MailStageStatus.SUCCEEDED

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def scalar(self, statement: object) -> CompletedHandoff:
            del statement
            return CompletedHandoff()

    session = Session()
    worker = MailRiskExtractionWorker(
        cast("async_sessionmaker[AsyncSession]", lambda: session),
        cast("SecretCipher", object()),
    )

    asyncio.run(
        worker._extract(
            UUID("00000000-0000-0000-0000-000000000010"),
            1,
            2,
            "body",
            [],
            "2026-08-12",
        )
    )


def test_mailbox_entrypoint_exposes_candidate_routes_without_app_composition() -> None:
    paths = {getattr(route, "path", "") for router in routers() for route in router.routes}

    assert "/mailbox/risk-candidates/{candidate_id}" in paths
    assert "/mailbox/risk-candidates/{candidate_id}/ignore" in paths
    assert "/mailbox/risk-candidates/{candidate_id}/confirm" in paths


def test_candidate_audit_is_closed_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    class FakeAuditService:
        def __init__(self, session: object) -> None:
            recorded["session"] = session

        async def record_success(self, **values: object) -> UUID:
            recorded.update(values)
            return UUID("00000000-0000-0000-0000-000000000099")

    monkeypatch.setattr("risk_platform.mailbox.extraction.AuditService", FakeAuditService)
    candidate = MailRiskCandidate(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        projectId=UUID("00000000-0000-0000-0000-000000000002"),
    )
    trace_id = UUID("00000000-0000-4000-8000-000000000003")

    asyncio.run(
        MailRiskCandidateService._audit_candidate(
            cast("AsyncSession", object()),
            actor_id=UUID("00000000-0000-0000-0000-000000000004"),
            trace_id=trace_id,
            action="MAIL_RISK_CONFIRMED",
            candidate=candidate,
        )
    )

    assert recorded == {
        "session": recorded["session"],
        "actor_id": UUID("00000000-0000-0000-0000-000000000004"),
        "actor_type": AuditActorType.USER,
        "module": "MAILBOX",
        "action": "MAIL_RISK_CONFIRMED",
        "resource_type": "MAIL_RISK_CANDIDATE",
        "resource_id": str(candidate.id),
        "trace_id": trace_id,
        "project_id": candidate.projectId,
    }


@pytest.fixture
def t026_postgresql() -> Iterator[tuple[str, AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """Exercise T026 only against an Alembic-created PostgreSQL schema."""

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL T026 validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t026_{uuid.uuid4().hex}"
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
            yield schema, engine, create_session_factory(engine)
        finally:
            asyncio.run(engine.dispose())
    finally:
        migration.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


class _FakeCipher:
    def decrypt_legacy(self, fields: object) -> str:
        del fields
        return "fake-provider-key"


class _FakeParser:
    async def _refetch(
        self, mailbox_id: UUID, uid_validity: int, imap_uid: int
    ) -> tuple[bytes, str]:
        del mailbox_id, uid_validity, imap_uid
        return (
            b"From: owner@example.com\r\nSubject: weekly\r\nMessage-ID: <t026>\r\n"
            b"Date: Tue, 11 Aug 2026 10:00:00 +0000\r\n\r\nA delivery risk requires review.",
            "<t026>",
        )


def _fake_parsed_mail(source: bytes, fallback_id: str) -> SimpleNamespace:
    """Keep this T026 worker acceptance test independent of T025 signal isolation."""

    del source, fallback_id
    return SimpleNamespace(
        sent_at=datetime(2026, 8, 11, tzinfo=UTC),
        body_text="A delivery risk requires review.",
        attachment_texts=[],
    )


class _FakeProvider:
    def __init__(self, outcome: str = "success") -> None:
        self.outcome = outcome
        self.calls = 0

    async def extract_risks(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: int,
        payload: dict[str, object],
    ) -> tuple[str, dict[str, int], int]:
        del endpoint, model, api_key, timeout_seconds
        self.calls += 1
        assert payload["schema_version"] == "MAIL_PROVIDER_DERIVED_CONTENT_V2"
        if self.outcome == "timeout":
            raise ProviderRequestError("UPSTREAM_TIMEOUT", retryable=True)
        if self.outcome == "invalid":
            return "not-json", {"input": 0, "output": 0, "total": 0}, 1
        return (
            '{"risks":[{"project_option_id":"P1","category_option_id":"C1",'
            '"level":"HIGH","description":"交付风险",'
            '"evidence":"里程碑延期", "suggestion":"立即处理", "confidence":90}]}',
            {"input": 10, "output": 8, "total": 18},
            1,
        )


@contextmanager
def _real_t026_worker(
    schema: str,
    factory: async_sessionmaker[AsyncSession],
    extractor: MailRiskExtractionWorker,
) -> Iterator[Celery]:
    """Run the registered T026 handler through a real isolated Celery solo worker."""

    celery = create_celery_app()
    queue = f"t026-worker-{uuid.uuid4().hex}"
    celery.conf.update(
        task_default_queue=queue,
        task_default_exchange=queue,
        task_default_routing_key=queue,
    )

    async def schema_bound_handler(payload: Mapping[str, object]) -> None:
        async with factory() as session:
            assert await session.scalar(text("SELECT current_schema()")) == schema
        await extractor.handle(payload)

    register_executor(
        celery,
        factory,
        {DurableTaskKind.MAIL_AI_REVIEW_PUBLISH.value: schema_bound_handler},
        owner=f"t026-worker-{queue}",
    )
    with start_worker(
        celery,
        pool="solo",
        concurrency=1,
        queues=[queue],
        perform_ping_check=False,
        loglevel="WARNING",
    ):
        yield celery


async def _wait_for_task(
    factory: async_sessionmaker[AsyncSession], task_id: UUID, expected: DurableTaskStatus
) -> DurableTask:
    for _ in range(100):
        async with factory() as session:
            task = await session.get(DurableTask, task_id)
            if task is not None and task.status is expected:
                return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"Celery worker did not reach {expected.value}")


async def _seed(
    factory: async_sessionmaker[AsyncSession], *, provider: bool = True
) -> dict[str, UUID]:
    suffix = uuid.uuid4().hex
    async with transaction(factory) as session:
        department = Department(code=f"T026-{suffix}", name="T026")
        owner = User(username=f"owner-{suffix}", passwordHash="not-used", displayName="Owner")
        session.add_all((department, owner))
        await session.flush()
        owner.departmentId = department.id
        project = Project(name=f"project-{suffix}", managerId=owner.id, deliveryOwnerName="Owner")
        category = RiskCategory(
            code=f"CAT-{suffix}", name="交付", defaultLevel=ProjectRiskLevel.MEDIUM, isActive=True
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
            subjectKeywords=[],
        )
        batch_task = DurableTask(
            kind=DurableTaskKind.MAILBOX_SYNC,
            idempotencyKey=f"batch-{suffix}",
            payload={},
            maxAttempts=1,
        )
        parse_task = DurableTask(
            kind=DurableTaskKind.ATTACHMENT_PARSE,
            idempotencyKey=f"parse-{suffix}",
            payload={},
            maxAttempts=1,
        )
        session.add_all((project, category, mailbox, batch_task, parse_task))
        await session.flush()
        batch = MailSyncBatch(
            taskId=batch_task.id,
            code=f"B-{suffix}",
            mailboxConfigId=mailbox.id,
            trigger=MailSyncTrigger.MANUAL,
        )
        session.add(batch)
        await session.flush()
        message = MailMessage(
            mailboxConfigId=mailbox.id,
            batchId=batch.id,
            messageId=f"<{suffix}>",
            uidValidity=42,
            imapUid=7,
            subject="weekly",
            receivedAt=datetime(2026, 8, 11, tzinfo=UTC),
            receivedAtSource=MailReceivedAtSource.FIRST_DURABLE_OBSERVATION,
        )
        handoff = MailSourceHandoff(
            mailboxConfigId=mailbox.id,
            batchId=batch.id,
            parseTaskId=parse_task.id,
            uidValidity=42,
            imapUid=7,
            receivedAt=datetime(2026, 8, 11, tzinfo=UTC),
            receivedAtSource=MailReceivedAtSource.FIRST_DURABLE_OBSERVATION,
            parseStatus=MailStageStatus.SUCCEEDED,
        )
        session.add_all((message, handoff))
        await session.flush()
        session.add(
            MailMessageProjectMatch(
                messageId=message.id,
                projectId=project.id,
                matchType=MailProjectMatchType.EXACT,
                confidence=100,
                matchedText=project.name,
            )
        )
        if provider:
            session.add(
                AiProviderConfig(
                    name=f"provider-{suffix}",
                    vendor="fake",
                    endpoint="https://provider.example.test",
                    model="fake-model",
                    encryptedApiKey="unused",
                    keyIv="unused",
                    keyAuthTag="unused",
                    keyLast4="used",
                    lastTestStatus=AiConnectionStatus.HEALTHY,
                    enabled=True,
                    isDefault=True,
                    priority=1,
                    retryCount=0,
                )
            )
        return {
            "owner": owner.id,
            "project": project.id,
            "category": category.id,
            "mailbox": mailbox.id,
            "message": message.id,
            "handoff": handoff.id,
        }


def _identity(owner_id: UUID, scope: str = "OWNED") -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(owner_id),
            username="owner",
            displayName="Owner",
            departmentName=None,
            roleCodes=["RISK_ADMIN"],
            permissions=["mailbox.sync_self", "risk.resolve"],
            dataScope=cast("DataScope", scope),
            mustChangePassword=False,
        ),
    )


async def _candidate(
    factory: async_sessionmaker[AsyncSession], seed: Mapping[str, UUID]
) -> UUID:
    async with transaction(factory) as session:
        item = MailRiskCandidate(
            messageId=seed["message"],
            projectId=seed["project"],
            categoryId=seed["category"],
            level=ProjectRiskLevel.HIGH,
            description="交付风险",
            evidence="里程碑延期",
            suggestion="立即处理",
            confidence=90,
        )
        session.add(item)
        await session.flush()
        return item.id


def test_postgresql_celery_fake_provider_success_and_duplicate_delivery(
    t026_postgresql: tuple[str, AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema, _engine, factory = t026_postgresql
    monkeypatch.setattr("risk_platform.mailbox.extraction.parse_mail", _fake_parsed_mail)

    async def scenario() -> None:
        seed = await _seed(factory)
        async with transaction(factory) as session:
            task = await enqueue_task(
                session,
                DurableTaskKind.MAIL_AI_REVIEW_PUBLISH,
                f"mail-ai:{seed['mailbox']}:42:7",
                {"mailbox_config_id": str(seed["mailbox"]), "uid_validity": 42, "imap_uid": 7},
            )
            task_id, generation = task.id, task.dispatchGeneration
        async with transaction(factory) as session:
            assert await publish_outbox(session, celery) == 1
        completed = await _wait_for_task(factory, task_id, DurableTaskStatus.SUCCEEDED)
        assert completed.dispatchGeneration == generation
        async with factory() as session:
            handoff = await session.get(MailSourceHandoff, seed["handoff"])
            assert completed is not None and completed.status is DurableTaskStatus.SUCCEEDED
            assert handoff is not None and handoff.aiReviewStatus is MailStageStatus.SUCCEEDED
            assert await session.scalar(select(func.count()).select_from(MailRiskCandidate)) == 1
            assert provider.calls == 1

    provider = _FakeProvider()
    extractor = MailRiskExtractionWorker(
        factory,
        cast("SecretCipher", _FakeCipher()),
        cast(AiProviderClient, provider),
        cast(MailParseWorker, _FakeParser()),
    )
    with _real_t026_worker(schema, factory, extractor) as celery:
        asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outcome", "with_provider", "stage", "code"),
    [
        ("success", False, MailStageStatus.PERMANENT_FAILURE, "PROVIDER_UNAVAILABLE"),
        ("timeout", True, MailStageStatus.RETRYABLE_FAILURE, "UPSTREAM_TIMEOUT"),
        ("invalid", True, MailStageStatus.PERMANENT_FAILURE, "PROVIDER_INVALID_OUTPUT"),
    ],
)
def test_postgresql_fake_provider_negative_outcomes(
    t026_postgresql: tuple[str, AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    with_provider: bool,
    stage: MailStageStatus,
    code: str,
) -> None:
    schema, _engine, factory = t026_postgresql
    monkeypatch.setattr("risk_platform.mailbox.extraction.parse_mail", _fake_parsed_mail)

    async def scenario() -> None:
        seed = await _seed(factory, provider=with_provider)
        async with transaction(factory) as session:
            task = await enqueue_task(
                session,
                DurableTaskKind.MAIL_AI_REVIEW_PUBLISH,
                f"mail-ai:{seed['mailbox']}:42:7",
                {"mailbox_config_id": str(seed["mailbox"]), "uid_validity": 42, "imap_uid": 7},
            )
            task_id = task.id
        async with transaction(factory) as session:
            assert await publish_outbox(session, celery) == 1
        expected = (
            DurableTaskStatus.RETRY_WAIT
            if outcome == "timeout"
            else DurableTaskStatus.SUCCEEDED
        )
        await _wait_for_task(factory, task_id, expected)
        async with factory() as session:
            handoff = await session.get(MailSourceHandoff, seed["handoff"])
            assert handoff is not None
            assert (handoff.aiReviewStatus, handoff.failureCode) == (stage, code)
            assert await session.scalar(select(func.count()).select_from(MailRiskCandidate)) == 0

    extractor = MailRiskExtractionWorker(
        factory,
        cast("SecretCipher", _FakeCipher()),
        cast(AiProviderClient, _FakeProvider(outcome)),
        cast(MailParseWorker, _FakeParser()),
    )
    with _real_t026_worker(schema, factory, extractor) as celery:
        asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["adjust", "ignore", "confirm"])
def test_candidate_mutations_and_metadata_audit_commit_and_rollback_atomically(
    t026_postgresql: tuple[str, AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    _schema, _engine, factory = t026_postgresql
    seed: dict[str, UUID] = {}

    async def invoke(
        service: MailRiskCandidateService, candidate_id: UUID, identity: SessionIdentity
    ) -> None:
        trace = uuid.uuid4()
        if operation == "adjust":
            await service.update(
                candidate_id,
                MailRiskCandidateUpdateRequest(
                    projectId=seed["project"],
                    categoryId=seed["category"],
                    level="LOW",
                    description="调整后的交付风险",
                    evidence="调整后的证据",
                    suggestion="调整后的建议",
                ),
                identity,
                trace,
            )
        elif operation == "ignore":
            await service.ignore(candidate_id, identity, trace)
        else:
            await service.confirm_response(candidate_id, identity, trace)

    async def scenario() -> None:
        nonlocal seed
        seed = await _seed(factory)
        candidate_id = await _candidate(factory, seed)
        identity = _identity(seed["owner"])
        service = MailRiskCandidateService(factory)

        async def fail_audit(self: AuditService, **values: object) -> UUID:
            del self, values
            raise RuntimeError("audit injection")

        monkeypatch.setattr(AuditService, "record_success", fail_audit)
        with pytest.raises(RuntimeError, match="audit injection"):
            await invoke(service, candidate_id, identity)
        async with factory() as session:
            candidate = await session.get(MailRiskCandidate, candidate_id)
            assert candidate is not None and candidate.status is MailRiskCandidateStatus.PENDING
            assert candidate.reviewedAt is None
            assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
            assert await session.scalar(select(func.count()).select_from(Risk)) == 0

        monkeypatch.undo()
        await invoke(service, candidate_id, identity)
        async with factory() as session:
            candidate = await session.get(MailRiskCandidate, candidate_id)
            assert candidate is not None
            assert candidate.reviewedAt is not None
            assert candidate.status is (
                MailRiskCandidateStatus.CONFIRMED
                if operation == "confirm"
                else MailRiskCandidateStatus.IGNORED
                if operation == "ignore"
                else MailRiskCandidateStatus.PENDING
            )
            assert await session.scalar(select(func.count()).select_from(AuditLog)) == (
                2 if operation == "confirm" else 1
            )
            assert await session.scalar(select(func.count()).select_from(Risk)) == (
                1 if operation == "confirm" else 0
            )

    asyncio.run(scenario())


def test_duplicate_confirm_is_idempotent_and_scope_hides_candidate(
    t026_postgresql: tuple[str, AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _schema, _engine, factory = t026_postgresql

    async def scenario() -> None:
        seed = await _seed(factory)
        candidate_id = await _candidate(factory, seed)
        service = MailRiskCandidateService(factory)
        identity = _identity(seed["owner"])
        first = await service.confirm_response(candidate_id, identity, uuid.uuid4())
        second = await service.confirm_response(candidate_id, identity, uuid.uuid4())
        assert first.confirmedRiskId == second.confirmedRiskId
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(Risk)) == 1
            assert await session.scalar(select(func.count()).select_from(AuditLog)) == 2

        scoped_out = _identity(seed["owner"], "NONE")
        for operation in ("adjust", "ignore", "confirm"):
            other_candidate = await _candidate(factory, seed)
            with pytest.raises(ApiError) as error:
                if operation == "adjust":
                    await service.update(
                        other_candidate,
                        MailRiskCandidateUpdateRequest(
                            projectId=seed["project"],
                            categoryId=seed["category"],
                            level="LOW",
                            description="越权调整风险",
                            evidence="越权证据",
                            suggestion="越权建议",
                        ),
                        scoped_out,
                        uuid.uuid4(),
                    )
                elif operation == "ignore":
                    await service.ignore(other_candidate, scoped_out, uuid.uuid4())
                else:
                    await service.confirm_response(other_candidate, scoped_out, uuid.uuid4())
            assert error.value.status_code == 404
            async with factory() as session:
                candidate = await session.get(MailRiskCandidate, other_candidate)
                assert candidate is not None and candidate.status is MailRiskCandidateStatus.PENDING
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(AuditLog)) == 2

    asyncio.run(scenario())
