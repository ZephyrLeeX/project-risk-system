"""T043 mailbox sync-results browse/retry surface.

Unit tests cover the pure mapping/decision helpers; PostgreSQL integration
tests exercise the browse queries and the ADR 0022 retry handoff against an
isolated, Alembic-created PostgreSQL 16 schema (skipped without
``TEST_DATABASE_URL``).
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User
from risk_platform.app import AppComposition, create_app
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser, RoleCode
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailboxEncryption,
    MailboxProvider,
    MailMessage,
    MailMessageProjectMatch,
    MailMessageSkipReason,
    MailMessageStatus,
    MailProjectMatchType,
    MailReceivedAtSource,
    MailRiskCandidate,
    MailRiskCandidateStatus,
    MailSourceHandoff,
    MailStageStatus,
    MailSyncBatch,
    MailSyncStatus,
    MailSyncTrigger,
)
from risk_platform.mailbox.sync_results import MailSyncResultsService
from risk_platform.mailbox.sync_results.api import get_sync_results_service, router
from risk_platform.mailbox.sync_results.schemas import MailMessageListQuery, MailSyncBatchListQuery
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.reliability.models import DurableTask, DurableTaskKind
from risk_platform.risks.models import ProjectRiskLevel, RiskCategory
from risk_platform.shared.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Unit tests (no database)                                                    #
# --------------------------------------------------------------------------- #


def _message(**overrides: object) -> MailMessage:
    base = SimpleNamespace(
        status=MailMessageStatus.COMPLETED,
        skipReason=None,
        failureSummary=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return cast("MailMessage", base)


def test_result_label_maps_each_terminal_state() -> None:
    label = MailSyncResultsService._result_label
    assert (
        label(_message(status=MailMessageStatus.FAILED, failureSummary="解析超时"), 0) == "解析超时"
    )
    assert label(_message(status=MailMessageStatus.FAILED, failureSummary=None), 0) == "处理失败"
    assert label(_message(status=MailMessageStatus.ANALYZING), 0) == "AI分析中"
    assert (
        label(
            _message(status=MailMessageStatus.SKIPPED, skipReason=MailMessageSkipReason.DUPLICATE),
            0,
        )
        == "重复邮件"
    )
    assert (
        label(
            _message(
                status=MailMessageStatus.SKIPPED, skipReason=MailMessageSkipReason.RULE_MISMATCH
            ),
            0,
        )
        == "不符合周报规则"
    )
    assert label(_message(), 3) == "提取3项风险"
    assert label(_message(), 0) == "未发现新增风险"


def test_result_note_maps_each_terminal_state() -> None:
    note = MailSyncResultsService._result_note
    assert note(_message(status=MailMessageStatus.FAILED), 0) == "等待风险管理员重试"
    assert note(_message(status=MailMessageStatus.ANALYZING), 0) == "已进入分析队列"
    assert (
        note(
            _message(status=MailMessageStatus.SKIPPED, skipReason=MailMessageSkipReason.DUPLICATE),
            0,
        )
        == "按Message-ID去重跳过"
    )
    assert (
        note(
            _message(
                status=MailMessageStatus.SKIPPED, skipReason=MailMessageSkipReason.RULE_MISMATCH
            ),
            0,
        )
        == "主题或发件人未命中识别规则"
    )
    assert note(_message(), 2) == "2项待风险管理员确认"
    assert note(_message(), 0) == "邮件分析完成"


def test_retry_stage_reparses_source_before_ai_retry() -> None:
    stage = MailSyncResultsService._retry_stage

    def handoff(parse: MailStageStatus, ai: MailStageStatus) -> MailSourceHandoff:
        return cast("MailSourceHandoff", SimpleNamespace(parseStatus=parse, aiReviewStatus=ai))

    # No handoff record: re-parse from the attachment stage.
    assert stage(None) == (DurableTaskKind.ATTACHMENT_PARSE, "parse")
    # Parse stage failed: retry parse, leave AI alone.
    assert stage(handoff(MailStageStatus.RETRYABLE_FAILURE, MailStageStatus.SUCCEEDED)) == (
        DurableTaskKind.ATTACHMENT_PARSE,
        "parse",
    )
    # AI failure re-fetches and re-parses the source before AI review.
    assert stage(handoff(MailStageStatus.SUCCEEDED, MailStageStatus.PERMANENT_FAILURE)) == (
        DurableTaskKind.ATTACHMENT_PARSE,
        "parse",
    )
    # Both succeeded (no failure): default back to the parse stage.
    assert stage(handoff(MailStageStatus.SUCCEEDED, MailStageStatus.SUCCEEDED)) == (
        DurableTaskKind.ATTACHMENT_PARSE,
        "parse",
    )


def test_mask_email_hides_local_part() -> None:
    assert MailSyncResultsService._mask_email("owner@example.com") == "ow***@example.com"


def test_require_risk_admin_rejects_non_admin_before_any_database_access() -> None:
    identity = SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(uuid.uuid4()),
            username="viewer",
            displayName="Viewer",
            departmentName=None,
            roleCodes=["PROJECT_MANAGER"],
            permissions=["mailbox.sync_self"],
            dataScope="OWNED",
            mustChangePassword=False,
        ),
    )
    service = MailSyncResultsService(cast("async_sessionmaker[AsyncSession]", None))
    with pytest.raises(ApiError) as exc:
        asyncio.run(service.summary(identity))
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# PostgreSQL integration fixtures                                             #
# --------------------------------------------------------------------------- #


@pytest.fixture
def t043_postgresql() -> Iterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    """Exercise T043 only against an Alembic-created PostgreSQL schema."""

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL T043 validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t043_{uuid.uuid4().hex}"
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
            yield schema, create_session_factory(engine)
        finally:
            asyncio.run(engine.dispose())
    finally:
        migration.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


async def _seed_owner(
    factory: async_sessionmaker[AsyncSession], *, admin: bool = True
) -> dict[str, object]:
    """Create an owner, department, project, category and mailbox config."""

    suffix = uuid.uuid4().hex
    async with transaction(factory) as session:
        department = Department(code=f"T043-{suffix}", name="T043")
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
        session.add_all((project, category, mailbox))
        await session.flush()
        return {
            "owner": owner.id,
            "project": project.id,
            "category": category.id,
            "mailbox": mailbox.id,
            "roles": ["RISK_ADMIN"] if admin else ["RISK_VIEWER"],
        }


def _identity(seed: dict[str, object], *, roles: list[str] | None = None) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(seed["owner"]),
            username="owner",
            displayName="Owner",
            departmentName=None,
            roleCodes=cast("list[RoleCode]", roles if roles is not None else seed["roles"]),
            permissions=["mailbox.sync_self"],
            dataScope="OWNED",
            mustChangePassword=False,
        ),
    )


async def _add_batch(
    factory: async_sessionmaker[AsyncSession],
    seed: dict[str, object],
    *,
    status: MailSyncStatus = MailSyncStatus.SUCCESS,
    trigger: MailSyncTrigger = MailSyncTrigger.MANUAL,
    code: str | None = None,
) -> tuple[MailSyncBatch, DurableTask]:
    suffix = uuid.uuid4().hex
    async with transaction(factory) as session:
        task = DurableTask(
            kind=DurableTaskKind.MAILBOX_SYNC,
            idempotencyKey=f"batch-{suffix}",
            payload={},
            maxAttempts=1,
        )
        session.add(task)
        await session.flush()
        batch = MailSyncBatch(
            taskId=task.id,
            code=code or f"B-{suffix}",
            mailboxConfigId=cast("uuid.UUID", seed["mailbox"]),
            trigger=trigger,
            status=status,
            scannedCount=4,
            newCount=4,
            successCount=1,
            skippedCount=2,
            failedCount=1,
            riskCandidateCount=1,
        )
        session.add(batch)
        await session.flush()
        return batch, task


async def _add_message(
    factory: async_sessionmaker[AsyncSession],
    seed: dict[str, object],
    batch_id: object,
    *,
    status: MailMessageStatus,
    skip_reason: MailMessageSkipReason | None = None,
    imap_uid: int = 7,
    failure_summary: str | None = None,
) -> MailMessage:
    suffix = uuid.uuid4().hex
    async with transaction(factory) as session:
        message = MailMessage(
            mailboxConfigId=cast("uuid.UUID", seed["mailbox"]),
            batchId=cast("uuid.UUID", batch_id),
            messageId=f"<{suffix}>",
            uidValidity=42,
            imapUid=imap_uid,
            subject=f"周报 {suffix}",
            senderName="Sender",
            senderAddress="sender@example.com",
            sentAt=datetime(2026, 8, 11, tzinfo=UTC),
            processedAt=datetime(2026, 8, 11, 1, tzinfo=UTC),
            status=status,
            skipReason=skip_reason,
            failureSummary=failure_summary,
            receivedAt=datetime(2026, 8, 11, tzinfo=UTC),
            receivedAtSource=MailReceivedAtSource.FIRST_DURABLE_OBSERVATION,
        )
        session.add(message)
        await session.flush()
        return message


async def _add_handoff(
    factory: async_sessionmaker[AsyncSession],
    seed: dict[str, object],
    batch_id: object,
    *,
    imap_uid: int,
    parse_status: MailStageStatus,
    ai_status: MailStageStatus = MailStageStatus.SUCCEEDED,
) -> MailSourceHandoff:
    suffix = uuid.uuid4().hex
    async with transaction(factory) as session:
        parse_task = DurableTask(
            kind=DurableTaskKind.ATTACHMENT_PARSE,
            idempotencyKey=f"parse-{suffix}",
            payload={},
            maxAttempts=1,
        )
        session.add(parse_task)
        await session.flush()
        handoff = MailSourceHandoff(
            mailboxConfigId=cast("uuid.UUID", seed["mailbox"]),
            batchId=cast("uuid.UUID", batch_id),
            parseTaskId=parse_task.id,
            uidValidity=42,
            imapUid=imap_uid,
            receivedAt=datetime(2026, 8, 11, tzinfo=UTC),
            receivedAtSource=MailReceivedAtSource.FIRST_DURABLE_OBSERVATION,
            parseStatus=parse_status,
            aiReviewStatus=ai_status,
            failureSummary="解析超时",
        )
        session.add(handoff)
        await session.flush()
        return handoff


# --------------------------------------------------------------------------- #
# PostgreSQL browse tests                                                     #
# --------------------------------------------------------------------------- #


def test_summary_empty_when_admin_has_no_mailbox_config(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        # An admin user with no mailbox config gets the empty summary.
        async with transaction(factory) as session:
            admin = User(
                username=f"admin-{uuid.uuid4().hex}", passwordHash="x", displayName="Admin"
            )
            session.add(admin)
            await session.flush()
            seed: dict[str, object] = {"owner": admin.id, "roles": ["RISK_ADMIN"]}
        service = MailSyncResultsService(factory)
        summary = await service.summary(_identity(seed))
        assert summary.configured is False
        assert summary.maskedEmail is None
        assert summary.latestBatch is None
        assert summary.historicalFailedCount == 0

    asyncio.run(scenario())


def test_summary_reports_latest_batch_counts(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        await _add_message(factory, seed, batch.id, status=MailMessageStatus.COMPLETED, imap_uid=7)
        await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.SKIPPED,
            skip_reason=MailMessageSkipReason.DUPLICATE,
            imap_uid=8,
        )
        await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.SKIPPED,
            skip_reason=MailMessageSkipReason.RULE_MISMATCH,
            imap_uid=9,
        )
        failed = await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.FAILED,
            imap_uid=10,
            failure_summary="解析失败",
        )
        # A pending risk candidate on the completed message.
        async with transaction(factory) as session:
            session.add(
                MailRiskCandidate(
                    messageId=failed.id,
                    projectId=cast("uuid.UUID", seed["project"]),
                    categoryId=cast("uuid.UUID", seed["category"]),
                    level=ProjectRiskLevel.HIGH,
                    description="d",
                    evidence="e",
                    suggestion="s",
                    confidence=80,
                    status=MailRiskCandidateStatus.PENDING,
                )
            )
        service = MailSyncResultsService(factory)
        summary = await service.summary(_identity(seed))
        assert summary.configured is True
        assert summary.maskedEmail == "ow***@example.com"
        assert summary.latestBatch is not None
        assert summary.latestDuplicateCount == 1
        assert summary.latestRuleMismatchCount == 1
        assert summary.latestFailedCount == 1
        # The pending candidate belongs to the failed message in the latest batch.
        assert summary.latestPendingRiskCount == 1
        assert summary.historicalFailedCount == 1

    asyncio.run(scenario())


def test_review_options_lists_active_projects_categories_and_levels(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        # An archived project must be hidden.
        suffix = uuid.uuid4().hex
        async with transaction(factory) as session:
            session.add(
                Project(
                    name=f"archived-{suffix}",
                    managerId=cast("uuid.UUID", seed["owner"]),
                    status=ProjectStatus.ARCHIVED,
                    deliveryOwnerName="Owner",
                )
            )
        service = MailSyncResultsService(factory)
        options = await service.review_options(_identity(seed))
        project_names = {p.name for p in options.projects}
        assert any(n.startswith("project-") for n in project_names)
        assert not any(n.startswith("archived-") for n in project_names)
        assert {lvl.value for lvl in options.levels} == {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
        assert options.categories  # the seeded active category appears

    asyncio.run(scenario())


def test_messages_supports_pagination_and_keyword_status_withrisk_filters(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        with_risk = await _add_message(
            factory, seed, batch.id, status=MailMessageStatus.COMPLETED, imap_uid=7
        )
        await _add_message(factory, seed, batch.id, status=MailMessageStatus.COMPLETED, imap_uid=8)
        skipped = await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.SKIPPED,
            skip_reason=MailMessageSkipReason.RULE_MISMATCH,
            imap_uid=9,
        )
        async with transaction(factory) as session:
            session.add(
                MailRiskCandidate(
                    messageId=with_risk.id,
                    projectId=cast("uuid.UUID", seed["project"]),
                    categoryId=cast("uuid.UUID", seed["category"]),
                    level=ProjectRiskLevel.MEDIUM,
                    description="d",
                    evidence="e",
                    suggestion="s",
                    confidence=70,
                    status=MailRiskCandidateStatus.PENDING,
                )
            )
        service = MailSyncResultsService(factory)

        all_msgs = await service.messages(
            _identity(seed), MailMessageListQuery(page=1, pageSize=10)
        )
        assert all_msgs.total == 3
        assert all_msgs.historicalFailedCount == 0
        # status filter
        skipped_only = await service.messages(
            _identity(seed),
            MailMessageListQuery(status="SKIPPED", page=1, pageSize=10),
        )
        assert skipped_only.total == 1
        assert skipped_only.items[0].id == str(skipped.id)
        assert skipped_only.items[0].resultLabel == "不符合周报规则"
        # withRisk filter
        risky = await service.messages(
            _identity(seed), MailMessageListQuery(withRisk=True, page=1, pageSize=10)
        )
        assert risky.total == 1
        assert risky.items[0].id == str(with_risk.id)
        assert risky.items[0].riskCandidateCount == 1
        assert risky.items[0].pendingRiskCount == 1
        # pagination
        page1 = await service.messages(_identity(seed), MailMessageListQuery(page=1, pageSize=2))
        page2 = await service.messages(_identity(seed), MailMessageListQuery(page=2, pageSize=2))
        assert len(page1.items) == 2
        assert len(page2.items) == 1
        assert {i.id for i in page1.items}.isdisjoint({i.id for i in page2.items})

    asyncio.run(scenario())


def test_message_detail_returns_matches_candidates_and_404_for_other_owner(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        message = await _add_message(
            factory, seed, batch.id, status=MailMessageStatus.COMPLETED, imap_uid=7
        )
        async with transaction(factory) as session:
            session.add(
                MailMessageProjectMatch(
                    messageId=message.id,
                    projectId=cast("uuid.UUID", seed["project"]),
                    matchType=MailProjectMatchType.EXACT,
                    confidence=100,
                    matchedText="project",
                )
            )
            session.add(
                MailRiskCandidate(
                    messageId=message.id,
                    projectId=cast("uuid.UUID", seed["project"]),
                    categoryId=cast("uuid.UUID", seed["category"]),
                    level=ProjectRiskLevel.HIGH,
                    description="d",
                    evidence="e",
                    suggestion="s",
                    confidence=90,
                    status=MailRiskCandidateStatus.PENDING,
                )
            )
        service = MailSyncResultsService(factory)
        detail = await service.message(_identity(seed), message.id)
        assert detail.subject.startswith("周报")
        assert len(detail.projectMatches) == 1
        assert detail.projectMatches[0].matchType == "EXACT"
        assert len(detail.riskCandidates) == 1
        assert detail.riskCandidates[0].levelLabel == "高风险"
        assert detail.retryCount == 0

        # A different owner cannot see this mailbox's message.
        other = await _seed_owner(factory)
        with pytest.raises(ApiError) as exc:
            await service.message(_identity(other), message.id)
        assert exc.value.status_code == 404

    asyncio.run(scenario())


def test_batches_list_and_detail_include_operator_and_messages(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        await _add_message(factory, seed, batch.id, status=MailMessageStatus.COMPLETED, imap_uid=7)
        service = MailSyncResultsService(factory)
        listed = await service.batches(_identity(seed), MailSyncBatchListQuery(page=1, pageSize=10))
        assert listed.total == 1
        assert listed.items[0].code == batch.code
        detail = await service.batch(_identity(seed), batch.id)
        assert detail.operatorName == "系统任务"
        assert len(detail.messages) == 1
        assert detail.startUid is None  # manual batch has no uid range

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# PostgreSQL retry tests (ADR 0022 handoff)                                   #
# --------------------------------------------------------------------------- #


def test_retry_rejects_non_failed_and_concurrent_then_creates_retry_batch(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        completed = await _add_message(
            factory, seed, batch.id, status=MailMessageStatus.COMPLETED, imap_uid=7
        )
        failed = await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.FAILED,
            imap_uid=8,
            failure_summary="解析失败",
        )
        await _add_handoff(
            factory, seed, batch.id, imap_uid=8, parse_status=MailStageStatus.RETRYABLE_FAILURE
        )
        service = MailSyncResultsService(factory)
        identity = _identity(seed)

        # Only FAILED messages can be retried.
        with pytest.raises(ApiError) as exc:
            await service.retry(completed.id, identity, uuid.uuid4())
        assert exc.value.status_code == 400

        # A concurrent queued batch blocks the retry.
        await _add_batch(factory, seed, status=MailSyncStatus.QUEUED)
        with pytest.raises(ApiError) as exc:
            await service.retry(failed.id, identity, uuid.uuid4())
        assert exc.value.status_code == 400
        assert "正在排队或运行" in exc.value.message

    asyncio.run(scenario())


def test_retry_creates_retry_batch_resets_failed_stage_and_audits(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        failed = await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.FAILED,
            imap_uid=8,
            failure_summary="解析失败",
        )
        handoff = await _add_handoff(
            factory, seed, batch.id, imap_uid=8, parse_status=MailStageStatus.RETRYABLE_FAILURE
        )
        service = MailSyncResultsService(factory)
        trace_id = uuid.uuid4()
        item = await service.retry(failed.id, _identity(seed), trace_id)
        assert item.trigger == "RETRY"
        assert item.status == "QUEUED"

        # The retry enqueued an ATTACHMENT_PARSE task (parse stage was failed).
        async with transaction(factory) as session:
            tasks = (
                (
                    await session.execute(
                        select(DurableTask).where(
                            DurableTask.kind == DurableTaskKind.ATTACHMENT_PARSE
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert any("mail-retry:" in (t.idempotencyKey or "") for t in tasks)
            # retryCount is owned by the processing pipeline, not the retry
            # endpoint (matches the legacy surface); it stays unchanged here.
            reloaded = await session.get(MailMessage, failed.id)
            assert reloaded is not None and reloaded.retryCount == 0
            # Failed parse stage reset to PENDING; failure diagnostics cleared.
            refreshed = await session.get(MailSourceHandoff, handoff.id)
            assert refreshed is not None
            assert refreshed.parseStatus is MailStageStatus.PENDING
            assert refreshed.failureSummary is None
            # Audit recorded metadata-only (ADR 0017).
            audited = (
                await session.execute(
                    text(
                        "SELECT action FROM audit_logs "
                        "WHERE module = 'MAIL_SYNC' AND \"resourceId\" = :rid"
                    ),
                    {"rid": str(failed.id)},
                )
            ).all()
            assert any(row[0] == "MAIL_MESSAGE_RETRIED" for row in audited)

    asyncio.run(scenario())


def test_retry_targets_ai_stage_when_only_ai_review_failed(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        failed = await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.FAILED,
            imap_uid=9,
        )
        await _add_handoff(
            factory,
            seed,
            batch.id,
            imap_uid=9,
            parse_status=MailStageStatus.SUCCEEDED,
            ai_status=MailStageStatus.RETRYABLE_FAILURE,
        )
        service = MailSyncResultsService(factory)
        await service.retry(failed.id, _identity(seed), uuid.uuid4())
        async with transaction(factory) as session:
            ai_tasks = (
                (
                    await session.execute(
                        select(DurableTask).where(
                            DurableTask.kind == DurableTaskKind.MAIL_AI_REVIEW_PUBLISH
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert any("mail-retry:" in (t.idempotencyKey or "") for t in ai_tasks)

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# HTTP-level routing, permission gating and envelope                          #
# --------------------------------------------------------------------------- #


def test_http_non_admin_is_forbidden_and_admin_retry_returns_envelope(
    t043_postgresql: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = t043_postgresql  # HTTP test reuses the schema via the bound factory

    async def scenario() -> None:
        seed = await _seed_owner(factory)
        batch, _ = await _add_batch(factory, seed)
        failed = await _add_message(
            factory,
            seed,
            batch.id,
            status=MailMessageStatus.FAILED,
            imap_uid=8,
            failure_summary="解析失败",
        )
        await _add_handoff(
            factory, seed, batch.id, imap_uid=8, parse_status=MailStageStatus.RETRYABLE_FAILURE
        )
        admin_identity = _identity(seed)
        viewer_identity = _identity(seed, roles=["PROJECT_MANAGER"])

        def override_factory(
            identity: SessionIdentity,
        ) -> Callable[[], Awaitable[SessionIdentity]]:
            async def _override() -> SessionIdentity:
                return identity

            return _override

        service = MailSyncResultsService(factory)
        app = create_app(
            Settings(environment="test", cors_origins=("https://web.internal",)),
            AppComposition(
                routers=(router,),
                dependency_overrides={
                    current_identity: override_factory(viewer_identity),
                    get_sync_results_service: lambda: service,
                },
            ),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            # Non-admin viewer is forbidden even with the mailbox permission.
            forbidden = await client.get("/api/mailbox/sync-summary")
            assert forbidden.status_code == 403
            assert forbidden.json()["code"] == "FORBIDDEN"

        # Swap to the admin identity and retry through the HTTP surface.
        app.dependency_overrides[current_identity] = override_factory(admin_identity)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            retried = await client.post(f"/api/mailbox/messages/{failed.id}/retry")
            assert retried.status_code == 200
            body = retried.json()
            assert body["code"] == "OK"
            assert body["message"] == "失败邮件已进入重新处理队列"
            assert body["data"]["trigger"] == "RETRY"

    asyncio.run(scenario())
