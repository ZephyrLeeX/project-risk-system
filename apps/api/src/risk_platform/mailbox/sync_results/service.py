"""T043 mailbox sync-results browse/retry application service.

Migrates the legacy NestJS ``mail-sync-results`` browse and retry surface to
FastAPI under ``mailbox.sync_self`` + ``RISK_ADMIN`` role gating, own-config
scope and ADR 0022 retry/handoff semantics. The service is read-mostly and
reuses the persisted T024-T026 mail facts (``MailSyncBatch``, ``MailMessage``,
``MailSourceHandoff``, ``MailRiskCandidate``); it owns no new persistence.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailMessage,
    MailMessageProjectMatch,
    MailMessageSkipReason,
    MailMessageStatus,
    MailProjectResolutionStatus,
    MailRiskCandidate,
    MailRiskCandidateStatus,
    MailSourceHandoff,
    MailStageStatus,
    MailSyncBatch,
    MailSyncStatus,
    MailSyncTrigger,
)
from risk_platform.mailbox.sync_results.schemas import (
    MailMessageDetail,
    MailMessageListItem,
    MailMessageListQuery,
    MailMessageListResponse,
    MailProjectMatchItem,
    MailProjectResolutionCandidateItem,
    MailRiskCandidateItem,
    MailRiskReviewOptions,
    MailSyncBatchDetail,
    MailSyncBatchItem,
    MailSyncBatchListQuery,
    MailSyncBatchListResponse,
    MailSyncSummary,
    ReviewOptionItem,
    RiskLevelOption,
)
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.risks.models import RiskCategory
from risk_platform.shared.errors import ApiError

# Literal alias for cast() of the static review-level tuples into the contract shape.
_CandidateLevelValue = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]

_LEVEL_LABELS: dict[str, str] = {
    "HIGH": "高风险",
    "MEDIUM": "中风险",
    "LOW": "低风险",
    "UNKNOWN": "待判断",
}
_REVIEW_LEVELS: tuple[tuple[str, str], ...] = (
    ("HIGH", "高风险"),
    ("MEDIUM", "中风险"),
    ("LOW", "低风险"),
    ("UNKNOWN", "待判断"),
)
_FAILURE_STAGES = frozenset({MailStageStatus.RETRYABLE_FAILURE, MailStageStatus.PERMANENT_FAILURE})


class MailSyncResultsService:
    """Browse sync results and retry failed messages for the caller's mailbox."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def summary(self, identity: SessionIdentity) -> MailSyncSummary:
        user_id = self._require_risk_admin(identity)
        async with self._sessions() as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == user_id)
            )
            if config is None:
                return self._empty_summary()
            latest = await session.scalar(
                select(MailSyncBatch)
                .where(MailSyncBatch.mailboxConfigId == config.id)
                .order_by(MailSyncBatch.createdAt.desc())
            )
            masked = self._mask_email(config.email)
            if latest is None:
                return MailSyncSummary(
                    configured=True,
                    maskedEmail=masked,
                    latestBatch=None,
                    latestDiscoveredCount=0,
                    latestHandedOffCount=0,
                    latestDuplicateCount=0,
                    latestDownstreamPendingCount=0,
                    latestScannedCount=0,
                    latestNewCount=0,
                    latestSuccessCount=0,
                    latestSkippedCount=0,
                    latestRuleMismatchCount=0,
                    latestFailedCount=0,
                    latestRiskCandidateCount=0,
                    latestPendingRiskCount=0,
                    historicalFailedCount=0,
                )
            mismatch = (
                await session.scalar(
                    select(func.count())
                    .select_from(MailMessage)
                    .where(
                        MailMessage.batchId == latest.id,
                        MailMessage.skipReason == MailMessageSkipReason.RULE_MISMATCH,
                    )
                )
                or 0
            )
            pending = (
                await session.scalar(
                    select(func.count())
                    .select_from(MailRiskCandidate)
                    .join(MailMessage, MailMessage.id == MailRiskCandidate.messageId)
                    .where(
                        MailMessage.mailboxConfigId == config.id,
                        MailMessage.batchId == latest.id,
                        MailRiskCandidate.status == MailRiskCandidateStatus.PENDING,
                    )
                )
                or 0
            )
            historical_failed = (
                await session.scalar(
                    select(func.count())
                    .select_from(MailMessage)
                    .where(
                        MailMessage.mailboxConfigId == config.id,
                        MailMessage.status == MailMessageStatus.FAILED,
                    )
                )
                or 0
            )
            return MailSyncSummary(
                configured=True,
                maskedEmail=masked,
                latestBatch=self._batch_item(latest),
                latestDiscoveredCount=latest.discoveredCount,
                latestHandedOffCount=latest.handedOffCount,
                latestDuplicateCount=max(latest.discoveredCount - latest.handedOffCount, 0),
                latestDownstreamPendingCount=latest.downstreamPendingCount,
                latestScannedCount=latest.scannedCount,
                latestNewCount=latest.newCount,
                latestSuccessCount=latest.successCount,
                latestSkippedCount=latest.skippedCount,
                latestRuleMismatchCount=int(mismatch),
                latestFailedCount=latest.failedCount,
                latestRiskCandidateCount=latest.riskCandidateCount,
                latestPendingRiskCount=int(pending),
                historicalFailedCount=int(historical_failed),
            )

    async def review_options(self, identity: SessionIdentity) -> MailRiskReviewOptions:
        user_id = self._require_risk_admin(identity)
        await self._own_config(user_id)
        async with self._sessions() as session:
            project_rows = (
                await session.execute(
                    select(Project.id, Project.name)
                    .where(Project.status != ProjectStatus.ARCHIVED)
                    .order_by(Project.name.asc())
                )
            ).all()
            category_rows = (
                await session.execute(
                    select(RiskCategory.id, RiskCategory.name)
                    .where(RiskCategory.isActive.is_(True))
                    .order_by(RiskCategory.sortOrder.asc(), RiskCategory.name.asc())
                )
            ).all()
        return MailRiskReviewOptions(
            projects=[
                ReviewOptionItem(id=str(pid), name=str(pname)) for pid, pname in project_rows
            ],
            categories=[
                ReviewOptionItem(id=str(cid), name=str(cname)) for cid, cname in category_rows
            ],
            levels=[
                RiskLevelOption(value=cast("_CandidateLevelValue", value), label=label)
                for value, label in _REVIEW_LEVELS
            ],
        )

    async def messages(
        self, identity: SessionIdentity, query: MailMessageListQuery
    ) -> MailMessageListResponse:
        user_id = self._require_risk_admin(identity)
        config = await self._own_config(user_id)
        async with self._sessions() as session:
            where = self._message_filter(config.id, query)
            total = (
                await session.scalar(select(func.count()).select_from(MailMessage).where(*where))
                or 0
            )
            historical_failed = (
                await session.scalar(
                    select(func.count())
                    .select_from(MailMessage)
                    .where(
                        MailMessage.mailboxConfigId == config.id,
                        MailMessage.status == MailMessageStatus.FAILED,
                    )
                )
                or 0
            )
            rows = (
                await session.execute(
                    select(MailMessage, MailSyncBatch.code)
                    .join(MailSyncBatch, MailSyncBatch.id == MailMessage.batchId)
                    .where(*where)
                    .order_by(MailMessage.sentAt.desc().nullslast(), MailMessage.createdAt.desc())
                    .offset((query.page - 1) * query.pageSize)
                    .limit(query.pageSize)
                )
            ).all()
            message_ids = [message.id for message, _ in rows]
            matches = await self._matches(session, message_ids)
            counts = await self._candidate_counts(session, message_ids)
            items = [
                self._list_item(
                    message,
                    batch_code,
                    matches.get(message.id, []),
                    counts.get(message.id, (0, 0)),
                )
                for message, batch_code in rows
            ]
        return MailMessageListResponse(
            items=items,
            page=query.page,
            pageSize=query.pageSize,
            total=int(total or 0),
            historicalFailedCount=int(historical_failed or 0),
        )

    async def message(self, identity: SessionIdentity, message_id: UUID) -> MailMessageDetail:
        user_id = self._require_risk_admin(identity)
        config = await self._own_config(user_id)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(MailMessage, MailSyncBatch.code)
                    .join(MailSyncBatch, MailSyncBatch.id == MailMessage.batchId)
                    .where(MailMessage.id == message_id, MailMessage.mailboxConfigId == config.id)
                )
            ).first()
            if row is None:
                raise ApiError(404, "NOT_FOUND", "邮件处理记录不存在")
            message, batch_code = row
            matches = (await self._matches(session, [message.id])).get(message.id, [])
            counts = (await self._candidate_counts(session, [message.id])).get(message.id, (0, 0))
            candidates = await self._candidates(session, message.id)
        return self._detail_item(message, batch_code, matches, counts, candidates)

    async def batches(
        self, identity: SessionIdentity, query: MailSyncBatchListQuery
    ) -> MailSyncBatchListResponse:
        user_id = self._require_risk_admin(identity)
        config = await self._own_config(user_id)
        async with self._sessions() as session:
            total = (
                await session.scalar(
                    select(func.count())
                    .select_from(MailSyncBatch)
                    .where(MailSyncBatch.mailboxConfigId == config.id)
                )
                or 0
            )
            rows = (
                await session.scalars(
                    select(MailSyncBatch)
                    .where(MailSyncBatch.mailboxConfigId == config.id)
                    .order_by(MailSyncBatch.createdAt.desc())
                    .offset((query.page - 1) * query.pageSize)
                    .limit(query.pageSize)
                )
            ).all()
        return MailSyncBatchListResponse(
            items=[self._batch_item(batch) for batch in rows],
            page=query.page,
            pageSize=query.pageSize,
            total=int(total),
        )

    async def batch(self, identity: SessionIdentity, batch_id: UUID) -> MailSyncBatchDetail:
        user_id = self._require_risk_admin(identity)
        config = await self._own_config(user_id)
        async with self._sessions() as session:
            batch = await session.scalar(
                select(MailSyncBatch).where(
                    MailSyncBatch.id == batch_id, MailSyncBatch.mailboxConfigId == config.id
                )
            )
            if batch is None:
                raise ApiError(404, "NOT_FOUND", "同步批次不存在")
            operator_name = "系统任务"
            if batch.operatorUserId is not None:
                operator_name = (
                    await session.scalar(
                        select(User.displayName).where(User.id == batch.operatorUserId)
                    )
                ) or "系统任务"
            message_rows = (
                await session.execute(
                    select(MailMessage, MailSyncBatch.code)
                    .join(MailSyncBatch, MailSyncBatch.id == MailMessage.batchId)
                    .where(MailMessage.batchId == batch.id)
                    .order_by(MailMessage.sentAt.desc().nullslast(), MailMessage.createdAt.desc())
                )
            ).all()
            message_ids = [message.id for message, _ in message_rows]
            matches = await self._matches(session, message_ids)
            counts = await self._candidate_counts(session, message_ids)
            messages = [
                self._list_item(
                    message,
                    batch_code,
                    matches.get(message.id, []),
                    counts.get(message.id, (0, 0)),
                )
                for message, batch_code in message_rows
            ]
        return MailSyncBatchDetail(
            **self._batch_item(batch).model_dump(),
            operatorName=operator_name,
            durationMs=batch.durationMs,
            startUid=str(batch.startUid) if batch.startUid is not None else None,
            endUid=str(batch.endUid) if batch.endUid is not None else None,
            messages=messages,
        )

    async def retry(
        self, message_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> MailSyncBatchItem:
        """Re-process one failed message under ADR 0022 source-identity handoff."""

        user_id = self._require_risk_admin(identity)
        async with transaction(self._sessions) as session:
            config = await self._own_config_for_update(session, user_id)
            message = await session.scalar(
                select(MailMessage).where(
                    MailMessage.id == message_id, MailMessage.mailboxConfigId == config.id
                )
            )
            if message is None:
                raise ApiError(404, "NOT_FOUND", "邮件处理记录不存在")
            running = await session.scalar(
                select(MailSyncBatch.id).where(
                    MailSyncBatch.mailboxConfigId == config.id,
                    MailSyncBatch.status.in_([MailSyncStatus.QUEUED, MailSyncStatus.RUNNING]),
                )
            )
            if running is not None:
                raise ApiError(400, "BAD_REQUEST", "当前已有同步任务正在排队或运行")
            uid_validity = message.uidValidity
            if uid_validity is None:
                raise ApiError(400, "BAD_REQUEST", "邮件缺少同步标识，无法重试")  # noqa: RUF001
            handoff = await session.scalar(
                select(MailSourceHandoff)
                .where(
                    MailSourceHandoff.mailboxConfigId == config.id,
                    MailSourceHandoff.uidValidity == uid_validity,
                    MailSourceHandoff.imapUid == message.imapUid,
                )
                .with_for_update()
            )
            if message.status is not MailMessageStatus.FAILED and not (
                handoff is not None
                and (
                    handoff.parseStatus in _FAILURE_STAGES
                    or handoff.aiReviewStatus in _FAILURE_STAGES
                )
            ):
                raise ApiError(400, "BAD_REQUEST", "仅处理失败的邮件可以重新处理")
            kind, reset_stage = self._retry_stage(handoff)
            task = await enqueue_task(
                session,
                kind,
                f"mail-retry:{config.id}:{uid_validity}:{message.imapUid}:{uuid4()}",
                {
                    "mailbox_config_id": str(config.id),
                    "uid_validity": uid_validity,
                    "imap_uid": message.imapUid,
                },
            )
            if handoff is not None:
                handoff.failureCode = None
                handoff.failureSummary = None
                if reset_stage == "parse":
                    handoff.parseStatus = MailStageStatus.PENDING
                else:
                    handoff.aiReviewStatus = MailStageStatus.PENDING
            message.status = MailMessageStatus.ANALYZING
            message.processedAt = None
            message.failureCode = message.failureSummary = None
            code = f"MAIL-RETRY-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:8].upper()}"
            batch = MailSyncBatch(
                taskId=task.id,
                code=code,
                mailboxConfigId=config.id,
                trigger=MailSyncTrigger.RETRY,
                operatorUserId=user_id,
                retryOfId=message.batchId,
                targetMessageId=message.id,
                startUid=message.imapUid,
                endUid=message.imapUid,
            )
            session.add(batch)
            await session.flush()
            # retryCount is incremented by the processing pipeline (ADR 0022
            # worker), not at the retry endpoint — matching the legacy surface.
            await AuditService(session).record_success(
                actor_id=user_id,
                actor_type=AuditActorType.USER,
                module="MAIL_SYNC",
                action="MAIL_MESSAGE_RETRIED",
                resource_type="MAIL_MESSAGE",
                resource_id=str(message.id),
                trace_id=trace_id,
            )
            return self._batch_item(batch)

    @staticmethod
    def _require_risk_admin(identity: SessionIdentity) -> UUID:
        if "RISK_ADMIN" not in identity.user.roleCodes:
            raise ApiError(403, "FORBIDDEN", "仅风险管理员可以查看本人邮箱同步结果")
        return UUID(identity.user.id)

    async def _own_config(self, user_id: UUID) -> MailboxConfig:
        async with self._sessions() as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == user_id)
            )
        if config is None:
            raise ApiError(404, "NOT_FOUND", "尚未保存个人邮箱配置")
        return config

    async def _own_config_for_update(self, session: AsyncSession, user_id: UUID) -> MailboxConfig:
        config = await session.scalar(
            select(MailboxConfig).where(MailboxConfig.userId == user_id).with_for_update()
        )
        if config is None:
            raise ApiError(404, "NOT_FOUND", "尚未保存个人邮箱配置")
        return config

    @staticmethod
    def _message_filter(config_id: UUID, query: MailMessageListQuery) -> list[Any]:
        filters: list[Any] = [MailMessage.mailboxConfigId == config_id]
        # FILTERED messages (re-judged non-weekly historical mail that never
        # produced a risk candidate) are hidden from the default list. They
        # remain queryable by an explicit status filter so an operator can
        # still surface them when auditing historical syncs. ``is_distinct_from``
        # treats NULL skipReason as kept (a non-filtered message), unlike ``!=``
        # which folds NULL rows out.
        if query.status is None:
            filters.append(
                MailMessage.skipReason.is_distinct_from(MailMessageSkipReason.FILTERED)
            )
        else:
            filters.append(MailMessage.status == query.status)
        if query.batchId is not None:
            filters.append(MailMessage.batchId == query.batchId)
        if query.withRisk:
            filters.append(
                MailMessage.id.in_(
                    select(MailRiskCandidate.messageId).where(
                        MailRiskCandidate.messageId == MailMessage.id
                    )
                )
            )
        keyword = (query.keyword or "").strip()
        if keyword:
            pattern = f"%{keyword}%"
            project_match = (
                select(MailMessageProjectMatch.messageId)
                .join(Project, Project.id == MailMessageProjectMatch.projectId)
                .where(Project.name.ilike(pattern))
                .scalar_subquery()
            )
            filters.append(
                or_(
                    MailMessage.subject.ilike(pattern),
                    MailMessage.senderName.ilike(pattern),
                    MailMessage.senderAddress.ilike(pattern),
                    MailMessage.id.in_(project_match),
                )
            )
        return filters

    @staticmethod
    async def _matches(
        session: AsyncSession, message_ids: list[UUID]
    ) -> dict[UUID, list[tuple[MailMessageProjectMatch, str]]]:
        if not message_ids:
            return {}
        rows = (
            await session.execute(
                select(MailMessageProjectMatch, Project.name)
                .join(Project, Project.id == MailMessageProjectMatch.projectId)
                .where(MailMessageProjectMatch.messageId.in_(message_ids))
                .order_by(MailMessageProjectMatch.confidence.desc())
            )
        ).all()
        grouped: dict[UUID, list[tuple[MailMessageProjectMatch, str]]] = defaultdict(list)
        for match, project_name in rows:
            grouped[match.messageId].append((match, project_name))
        return grouped

    @staticmethod
    async def _candidate_counts(
        session: AsyncSession, message_ids: list[UUID]
    ) -> dict[UUID, tuple[int, int]]:
        if not message_ids:
            return {}
        rows = (
            await session.execute(
                select(
                    MailRiskCandidate.messageId,
                    func.count(),
                    func.count().filter(
                        MailRiskCandidate.status == MailRiskCandidateStatus.PENDING
                    ),
                )
                .where(MailRiskCandidate.messageId.in_(message_ids))
                .group_by(MailRiskCandidate.messageId)
            )
        ).all()
        return {
            message_id: (int(total or 0), int(pending or 0)) for message_id, total, pending in rows
        }

    @staticmethod
    async def _candidates(
        session: AsyncSession, message_id: UUID
    ) -> list[tuple[MailRiskCandidate, str, str]]:
        rows = (
            await session.execute(
                select(MailRiskCandidate, Project.name, RiskCategory.name)
                .join(Project, Project.id == MailRiskCandidate.projectId)
                .join(RiskCategory, RiskCategory.id == MailRiskCandidate.categoryId)
                .where(MailRiskCandidate.messageId == message_id)
                .order_by(MailRiskCandidate.createdAt.asc())
            )
        ).all()
        return [(candidate, pname, cname) for candidate, pname, cname in rows]

    @staticmethod
    def _empty_summary() -> MailSyncSummary:
        return MailSyncSummary(
            configured=False,
            maskedEmail=None,
            latestBatch=None,
            latestDiscoveredCount=0,
            latestHandedOffCount=0,
            latestDownstreamPendingCount=0,
            latestScannedCount=0,
            latestNewCount=0,
            latestSuccessCount=0,
            latestSkippedCount=0,
            latestDuplicateCount=0,
            latestRuleMismatchCount=0,
            latestFailedCount=0,
            latestRiskCandidateCount=0,
            latestPendingRiskCount=0,
            historicalFailedCount=0,
        )

    @staticmethod
    def _mask_email(email: str) -> str:
        return f"{email[:2]}***@{email.split('@')[-1]}"

    @staticmethod
    def _batch_item(batch: MailSyncBatch) -> MailSyncBatchItem:
        return MailSyncBatchItem(
            id=str(batch.id),
            code=batch.code,
            trigger=batch.trigger.value,
            status=batch.status.value,
            createdAt=batch.createdAt.isoformat()
            if batch.createdAt
            else datetime.now(UTC).isoformat(),
            startedAt=batch.startedAt.isoformat() if batch.startedAt else None,
            finishedAt=batch.finishedAt.isoformat() if batch.finishedAt else None,
            discoveredCount=batch.discoveredCount,
            handedOffCount=batch.handedOffCount,
            duplicateCount=max(batch.discoveredCount - batch.handedOffCount, 0),
            downstreamPendingCount=batch.downstreamPendingCount,
            scannedCount=batch.scannedCount,
            newCount=batch.newCount,
            successCount=batch.successCount,
            skippedCount=batch.skippedCount,
            failedCount=batch.failedCount,
            riskCandidateCount=batch.riskCandidateCount,
            errorSummary=batch.errorSummary,
        )

    @classmethod
    def _list_item(
        cls,
        message: MailMessage,
        batch_code: str,
        matches: list[tuple[MailMessageProjectMatch, str]],
        counts: tuple[int, int],
    ) -> MailMessageListItem:
        total, pending = counts
        return MailMessageListItem(
            id=str(message.id),
            batchId=str(message.batchId),
            batchCode=batch_code,
            status=message.status.value,
            subject=message.subject,
            senderName=message.senderName,
            senderAddress=message.senderAddress,
            sentAt=message.sentAt.isoformat() if message.sentAt else None,
            processedAt=message.processedAt.isoformat() if message.processedAt else None,
            projectMatches=[
                MailProjectMatchItem(
                    id=str(match.id),
                    projectId=str(match.projectId),
                    projectName=project_name,
                    matchType=match.matchType.value,
                    confidence=match.confidence,
                    matchedText=match.matchedText,
                )
                for match, project_name in matches
            ],
            projectResolutionCandidates=cls._resolution_candidates(
                message.projectResolutionCandidates
            ),
            riskCandidateCount=total,
            pendingRiskCount=pending,
            resultLabel=cls._result_label(message, total),
            resultNote=cls._result_note(message, pending),
            failureSummary=message.failureSummary,
        )

    @staticmethod
    def _resolution_candidates(
        value: JSONValue | None,
    ) -> list[MailProjectResolutionCandidateItem]:
        if not isinstance(value, list):
            return []
        result: list[MailProjectResolutionCandidateItem] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            required = {"optionId", "projectId", "name", "externalCode", "alias", "status"}
            if not required.issubset(item):
                continue
            if not all(isinstance(item[key], (str, type(None))) for key in required):
                continue
            if not isinstance(item["optionId"], str) or not isinstance(item["projectId"], str):
                continue
            if not isinstance(item["name"], str) or not isinstance(item["status"], str):
                continue
            result.append(
                MailProjectResolutionCandidateItem(
                    optionId=item["optionId"],
                    projectId=item["projectId"],
                    name=item["name"],
                    externalCode=item["externalCode"]
                    if isinstance(item["externalCode"], str)
                    else None,
                    alias=item["alias"] if isinstance(item["alias"], str) else None,
                    status=item["status"],
                )
            )
        return result

    @classmethod
    def _detail_item(
        cls,
        message: MailMessage,
        batch_code: str,
        matches: list[tuple[MailMessageProjectMatch, str]],
        counts: tuple[int, int],
        candidates: list[tuple[MailRiskCandidate, str, str]],
    ) -> MailMessageDetail:
        base = cls._list_item(message, batch_code, matches, counts)
        return MailMessageDetail(
            **base.model_dump(),
            keyPoints=cls._string_array(message.keyPoints),
            sanitizedSummary=message.sanitizedSummary,
            attachments=cls._typed_array(message.attachmentMetadata),
            processingTrace=cls._typed_array(message.processingTrace),
            riskCandidates=[
                cls._candidate_item(candidate, project_name, category_name)
                for candidate, project_name, category_name in candidates
            ],
            retryCount=message.retryCount,
        )

    @staticmethod
    def _candidate_item(
        candidate: MailRiskCandidate, project_name: str, category_name: str
    ) -> MailRiskCandidateItem:
        return MailRiskCandidateItem(
            id=str(candidate.id),
            projectId=str(candidate.projectId),
            projectName=project_name,
            categoryId=str(candidate.categoryId),
            categoryName=category_name,
            level=candidate.level.value,
            levelLabel=_LEVEL_LABELS[candidate.level.value],
            description=candidate.description,
            evidence=candidate.evidence,
            suggestion=candidate.suggestion,
            confidence=candidate.confidence,
            status=candidate.status.value,
            confirmedRiskId=str(candidate.confirmedRiskId) if candidate.confirmedRiskId else None,
            reviewedAt=candidate.reviewedAt.isoformat() if candidate.reviewedAt else None,
        )

    @staticmethod
    def _result_label(message: MailMessage, candidate_count: int) -> str:
        if (
            getattr(message, "projectResolutionStatus", None)
            is MailProjectResolutionStatus.WAITING_CONFIRMATION
        ):
            return "等待确认所属项目"
        if message.status is MailMessageStatus.FAILED:
            return message.failureSummary or "处理失败"
        if message.status is MailMessageStatus.ANALYZING:
            return "AI分析中"
        if message.status is MailMessageStatus.SKIPPED:
            if message.skipReason is MailMessageSkipReason.DUPLICATE:
                return "重复邮件"
            if message.skipReason is MailMessageSkipReason.FILTERED:
                return "已按周报规则过滤"
            return "不符合周报规则"
        return f"提取{candidate_count}项风险" if candidate_count else "未发现新增风险"

    @staticmethod
    def _result_note(message: MailMessage, pending: int) -> str:
        if (
            getattr(message, "projectResolutionStatus", None)
            is MailProjectResolutionStatus.WAITING_CONFIRMATION
        ):
            return "请选择候选项目后继续风险识别"
        if message.status is MailMessageStatus.FAILED:
            return "等待风险管理员重试"
        if message.status is MailMessageStatus.ANALYZING:
            return "已进入分析队列"
        if message.status is MailMessageStatus.SKIPPED:
            if message.skipReason is MailMessageSkipReason.DUPLICATE:
                return "按Message-ID去重跳过"
            if message.skipReason is MailMessageSkipReason.FILTERED:
                return "重新判定为非周报，已从默认列表隐藏"
            return "主题或发件人未命中识别规则"
        return f"{pending}项待风险管理员确认" if pending else "邮件分析完成"

    @staticmethod
    def _string_array(value: JSONValue | None) -> list[str]:
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    @staticmethod
    def _typed_array(value: JSONValue | None) -> list[Any]:
        """Return stored JSON arrays for Pydantic to validate against the contract."""

        return value if isinstance(value, list) else []

    @staticmethod
    def _retry_stage(
        handoff: MailSourceHandoff | None,
    ) -> tuple[DurableTaskKind, str]:
        """ADR 0022: retry only the failed downstream stage by source identity."""

        if handoff is None:
            return DurableTaskKind.ATTACHMENT_PARSE, "parse"
        if handoff.parseStatus in _FAILURE_STAGES:
            return DurableTaskKind.ATTACHMENT_PARSE, "parse"
        if handoff.aiReviewStatus in _FAILURE_STAGES:
            return DurableTaskKind.ATTACHMENT_PARSE, "parse"
        return DurableTaskKind.ATTACHMENT_PARSE, "parse"


__all__ = ["MailSyncResultsService"]
