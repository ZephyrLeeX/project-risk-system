"""PostgreSQL materialization and scoped weekly-report queries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Never, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.mailbox.models import (
    MailMessage,
    MailMessageProjectMatch,
    MailMessageStatus,
    MailRiskCandidate,
    MailRiskCandidateStatus,
)
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import get_scoped_project, project_scope_predicate
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.risks.models import ProjectRiskLevel, Risk
from risk_platform.shared.errors import ApiError
from risk_platform.todos.models import ActionItem

from .models import WeeklyReportAggregate, WeeklyReportItem
from .schemas import (
    WeeklyProject,
    WeeklyProjectDetail,
    WeeklyProjectSummary,
    WeeklyReportItemResponse,
    WeeklyReportResponse,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
FRESHNESS = timedelta(minutes=15)
RECONCILIATION_WEEKS = 14

AuthorityRow = tuple[MailMessage, MailRiskCandidate, Risk, ActionItem]


def shanghai_week_start(value: datetime) -> date:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("weekly ownership requires an aware instant")
    local = value.astimezone(SHANGHAI)
    return local.date() - timedelta(days=local.weekday())


async def invalidate_week(
    session: AsyncSession, week_start: date, project_id: UUID
) -> None:
    """Mark one materialization stale and enqueue exactly its next revision."""

    aggregate = await session.scalar(
        select(WeeklyReportAggregate)
        .where(
            WeeklyReportAggregate.weekStart == week_start,
            WeeklyReportAggregate.projectId == project_id,
        )
        .with_for_update()
    )
    target_revision = 1
    if aggregate is not None:
        aggregate.stale = True
        target_revision = aggregate.sourceRevision + 1
    await enqueue_task(
        session,
        DurableTaskKind.WEEKLY_REPORT_REBUILD,
        f"weekly-report:{week_start.isoformat()}:{project_id}:{target_revision}",
        {
            "week_start": week_start.isoformat(),
            "project_id": str(project_id),
            "source_revision": target_revision,
        },
    )


async def invalidate_message_project(
    session: AsyncSession, message: MailMessage, project_id: UUID
) -> None:
    await invalidate_week(
        session, shanghai_week_start(message.sentAt or message.receivedAt), project_id
    )


async def invalidate_candidate(
    session: AsyncSession, candidate: MailRiskCandidate, *, old_project_id: UUID | None = None
) -> None:
    message = await session.get(MailMessage, candidate.messageId)
    if message is None:
        raise RuntimeError("weekly source mail is missing")
    project_ids = {candidate.projectId}
    if old_project_id is not None:
        project_ids.add(old_project_id)
    for project_id in sorted(project_ids, key=str):
        await invalidate_message_project(session, message, project_id)


async def invalidate_risk(session: AsyncSession, risk_id: UUID) -> None:
    candidate = await session.scalar(
        select(MailRiskCandidate).where(MailRiskCandidate.confirmedRiskId == risk_id)
    )
    if candidate is not None:
        await invalidate_candidate(session, candidate)


class WeeklyReportService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def current(self, identity: SessionIdentity) -> WeeklyReportResponse:
        return await self.report(identity, shanghai_week_start(self._clock()))

    async def report(
        self, identity: SessionIdentity, week_start: date
    ) -> WeeklyReportResponse:
        self._validate_week_start(week_start)
        now = self._clock()
        scope = project_scope_predicate(
            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
        )
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(WeeklyReportAggregate, Project)
                    .join(Project, Project.id == WeeklyReportAggregate.projectId)
                    .where(WeeklyReportAggregate.weekStart == week_start, scope)
                    .order_by(Project.name, Project.id)
                )
            ).all()
            aggregate_ids = [row[0].id for row in rows]
            report_count = (
                await session.scalar(
                    select(func.count(func.distinct(WeeklyReportItem.sourceMailId))).where(
                        WeeklyReportItem.aggregateId.in_(aggregate_ids)
                    )
                )
                if aggregate_ids
                else 0
            )
        if not rows:
            self._stale_error(week_start, None)
        aggregates = [row[0] for row in rows]
        expired = [row for row in aggregates if row.freshnessDeadline < now]
        if expired:
            self._stale_error(week_start, None)
        generated_at = min(row.generatedAt for row in aggregates)
        deadline = min(row.freshnessDeadline for row in aggregates)
        projects = [self._project_summary(aggregate, project) for aggregate, project in rows]
        total_levels: Counter[str] = Counter()
        for aggregate in aggregates:
            total_levels.update(
                {key: int(value) for key, value in aggregate.riskLevelCounts.items()}
            )
        return WeeklyReportResponse(
            weekStart=week_start,
            weekEnd=week_start + timedelta(days=7),
            generatedAt=generated_at,
            stale=any(row.stale for row in aggregates),
            freshnessDeadline=deadline,
            summary={
                "projectCount": len(projects),
                "reportCount": int(report_count or 0),
                "riskCount": sum(row.riskCount for row in aggregates),
                "riskLevelCounts": dict(total_levels),
            },
            projects=projects,
        )

    async def detail(
        self, identity: SessionIdentity, week_start: date, project_id: UUID
    ) -> WeeklyProjectDetail:
        self._validate_week_start(week_start)
        now = self._clock()
        async with self._sessions() as session:
            project = await get_scoped_project(
                session,
                project_id,
                UUID(identity.user.id),
                DataScopeType(identity.user.dataScope),
            )
            if project is None:
                raise ApiError(404, "PROJECT_NOT_FOUND", "项目不存在或不在当前数据范围内")
            aggregate = await session.scalar(
                select(WeeklyReportAggregate).where(
                    WeeklyReportAggregate.weekStart == week_start,
                    WeeklyReportAggregate.projectId == project_id,
                )
            )
            if aggregate is None:
                self._stale_error(week_start, project_id)
            if aggregate.freshnessDeadline < now:
                self._stale_error(week_start, project_id)
            items = (
                await session.scalars(
                    select(WeeklyReportItem)
                    .where(WeeklyReportItem.aggregateId == aggregate.id)
                    .order_by(WeeklyReportItem.occurredAt.desc(), WeeklyReportItem.id.desc())
                )
            ).all()
        return WeeklyProjectDetail(
            weekStart=week_start,
            project=WeeklyProject(id=project.id, name=project.name),
            items=[self._item(item) for item in items],
            generatedAt=aggregate.generatedAt,
            stale=aggregate.stale,
        )

    async def rebuild(
        self, week_start: date, project_id: UUID, target_revision: int
    ) -> None:
        self._validate_week_start(week_start)
        if target_revision < 1:
            raise ValueError("source_revision must be positive")
        generated_at = self._clock()
        async with transaction(self._sessions) as session:
            aggregate = await session.scalar(
                select(WeeklyReportAggregate)
                .where(
                    WeeklyReportAggregate.weekStart == week_start,
                    WeeklyReportAggregate.projectId == project_id,
                )
                .with_for_update()
            )
            existed = aggregate is not None
            if (
                aggregate is not None
                and aggregate.sourceRevision >= target_revision
                and not aggregate.stale
            ):
                return
            rows = await self._authority_rows(session, week_start, project_id)
            if aggregate is None:
                aggregate = WeeklyReportAggregate(
                    weekStart=week_start,
                    projectId=project_id,
                    summary={},
                    riskCount=0,
                    riskLevelCounts={},
                    sourceRevision=target_revision,
                    stale=True,
                    generatedAt=generated_at,
                    freshnessDeadline=generated_at + FRESHNESS,
                )
                session.add(aggregate)
                await session.flush()
            else:
                await session.execute(
                    delete(WeeklyReportItem).where(
                        WeeklyReportItem.aggregateId == aggregate.id
                    )
                )
            revision = target_revision
            if existed and aggregate.sourceRevision >= target_revision:
                revision = aggregate.sourceRevision + 1
            levels = Counter(row[2].level.value for row in rows)
            statuses = Counter(row[3].status.value for row in rows)
            report_count = len({row[0].id for row in rows})
            aggregate.summary = {
                "reportCount": report_count,
                "activeRiskCount": sum(row[2].status.value == "ACTIVE" for row in rows),
                "resolvedRiskCount": sum(row[2].status.value == "RESOLVED" for row in rows),
                "todoStatusCounts": dict(statuses),
            }
            aggregate.riskCount = len(rows)
            aggregate.riskLevelCounts = {
                level.value: levels[level.value] for level in ProjectRiskLevel
            }
            aggregate.sourceRevision = revision
            aggregate.generatedAt = generated_at
            aggregate.freshnessDeadline = generated_at + FRESHNESS
            aggregate.stale = False
            for message, candidate, risk, todo in rows:
                session.add(
                    WeeklyReportItem(
                        aggregateId=aggregate.id,
                        sourceMailId=message.id,
                        sourceCandidateId=candidate.id,
                        riskId=risk.id,
                        todoId=todo.id,
                        sourceRevision=revision,
                        summary=message.sanitizedSummary or candidate.description,
                        riskLevel=risk.level,
                        riskStatus=risk.status,
                        todoStatus=todo.status,
                        occurredAt=message.sentAt or message.receivedAt,
                    )
                )

    async def reconcile(self, as_of: datetime | None = None) -> int:
        observed_at = as_of or self._clock()
        oldest = shanghai_week_start(observed_at) - timedelta(
            weeks=RECONCILIATION_WEEKS - 1
        )
        enqueued = 0
        async with transaction(self._sessions) as session:
            aggregates = (
                await session.scalars(
                    select(WeeklyReportAggregate)
                    .where(WeeklyReportAggregate.weekStart >= oldest)
                    .order_by(WeeklyReportAggregate.weekStart, WeeklyReportAggregate.projectId)
                    .with_for_update()
                )
            ).all()
            aggregate_map = {
                (aggregate.weekStart, aggregate.projectId): aggregate for aggregate in aggregates
            }
            start = datetime.combine(oldest, datetime.min.time(), SHANGHAI).astimezone(UTC)
            source_rows = (
                await session.execute(
                    select(
                        MailRiskCandidate.projectId,
                        func.coalesce(MailMessage.sentAt, MailMessage.receivedAt),
                        MailMessage.id,
                        MailRiskCandidate.id,
                        Risk.id,
                        ActionItem.id,
                        func.greatest(
                            MailMessage.updatedAt,
                            MailRiskCandidate.updatedAt,
                            Risk.updatedAt,
                            ActionItem.updatedAt,
                        ),
                    )
                    .join(MailRiskCandidate, MailRiskCandidate.messageId == MailMessage.id)
                    .join(Risk, Risk.id == MailRiskCandidate.confirmedRiskId)
                    .join(ActionItem, ActionItem.riskId == Risk.id)
                    .where(
                        MailMessage.status == MailMessageStatus.COMPLETED,
                        MailRiskCandidate.status == MailRiskCandidateStatus.CONFIRMED,
                        func.coalesce(MailMessage.sentAt, MailMessage.receivedAt) >= start,
                        exists(
                            select(MailMessageProjectMatch.id).where(
                                MailMessageProjectMatch.messageId == MailMessage.id,
                                MailMessageProjectMatch.projectId
                                == MailRiskCandidate.projectId,
                            )
                        ),
                    )
                )
            ).all()
            current_sources: dict[
                tuple[date, UUID], set[tuple[UUID, UUID, UUID, UUID]]
            ] = {}
            latest_changes: dict[tuple[date, UUID], datetime] = {}
            for (
                project_id,
                occurred_at,
                message_id,
                candidate_id,
                risk_id,
                todo_id,
                changed_at,
            ) in source_rows:
                key = (shanghai_week_start(occurred_at), project_id)
                current_sources.setdefault(key, set()).add(
                    (message_id, candidate_id, risk_id, todo_id)
                )
                latest_changes[key] = max(latest_changes.get(key, changed_at), changed_at)

            stored_sources: dict[
                tuple[date, UUID], set[tuple[UUID, UUID, UUID, UUID]]
            ] = {key: set() for key in aggregate_map}
            if aggregates:
                item_rows = (
                    await session.execute(
                        select(
                            WeeklyReportItem.aggregateId,
                            WeeklyReportItem.sourceMailId,
                            WeeklyReportItem.sourceCandidateId,
                            WeeklyReportItem.riskId,
                            WeeklyReportItem.todoId,
                        ).where(
                            WeeklyReportItem.aggregateId.in_(
                                [aggregate.id for aggregate in aggregates]
                            )
                        )
                    )
                ).all()
                keys_by_id = {aggregate.id: key for key, aggregate in aggregate_map.items()}
                for aggregate_id, message_id, candidate_id, risk_id, todo_id in item_rows:
                    stored_sources[keys_by_id[aggregate_id]].add(
                        (message_id, candidate_id, risk_id, todo_id)
                    )

            invalidated: set[tuple[date, UUID]] = set()
            keys = set(aggregate_map) | set(current_sources)
            for key in sorted(keys, key=lambda item: (item[0], str(item[1]))):
                aggregate = aggregate_map.get(key)
                changed_at = latest_changes.get(key)
                is_changed = stored_sources.get(key, set()) != current_sources.get(key, set())
                is_expired = aggregate is not None and (
                    aggregate.stale or aggregate.freshnessDeadline < observed_at
                )
                is_newer = (
                    aggregate is not None
                    and changed_at is not None
                    and changed_at > aggregate.generatedAt
                )
                if aggregate is None or is_changed or is_expired or is_newer:
                    await invalidate_week(session, *key)
                    invalidated.add(key)
                    enqueued += 1
        return enqueued

    async def handle(self, payload: Mapping[str, JSONValue]) -> None:
        try:
            week_start = date.fromisoformat(cast(str, payload["week_start"]))
            project_id = UUID(cast(str, payload["project_id"]))
            source_revision = int(cast(int, payload["source_revision"]))
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid WEEKLY_REPORT_REBUILD payload") from None
        await self.rebuild(week_start, project_id, source_revision)

    async def _authority_rows(
        self, session: AsyncSession, week_start: date, project_id: UUID
    ) -> Sequence[AuthorityRow]:
        start = datetime.combine(week_start, datetime.min.time(), SHANGHAI).astimezone(UTC)
        end = start + timedelta(days=7)
        occurred_at = func.coalesce(MailMessage.sentAt, MailMessage.receivedAt)
        result = await session.execute(
            select(MailMessage, MailRiskCandidate, Risk, ActionItem)
            .join(MailRiskCandidate, MailRiskCandidate.messageId == MailMessage.id)
            .join(Risk, Risk.id == MailRiskCandidate.confirmedRiskId)
            .join(ActionItem, ActionItem.riskId == Risk.id)
            .where(
                MailMessage.status == MailMessageStatus.COMPLETED,
                MailRiskCandidate.status == MailRiskCandidateStatus.CONFIRMED,
                MailRiskCandidate.projectId == project_id,
                occurred_at >= start,
                occurred_at < end,
                exists(
                    select(MailMessageProjectMatch.id).where(
                        MailMessageProjectMatch.messageId == MailMessage.id,
                        MailMessageProjectMatch.projectId == project_id,
                    )
                ),
            )
            .order_by(occurred_at, MailRiskCandidate.id)
        )
        return [cast(AuthorityRow, row) for row in result.all()]

    @staticmethod
    def _validate_week_start(week_start: date) -> None:
        if week_start.weekday() != 0:
            raise ApiError(422, "VALIDATION_ERROR", "weekStart 必须是上海时区周一")

    @staticmethod
    def _stale_error(week_start: date, project_id: UUID | None) -> Never:
        data: dict[str, JSONValue] = {
            "weekStart": week_start.isoformat(),
            "projectId": str(project_id) if project_id is not None else None,
            "retryAfterSeconds": 60,
        }
        raise ApiError(
            503,
            "WEEKLY_REPORT_STALE",
            "周报汇总正在重建, 请稍后重试",
            data=data,
            headers={"Retry-After": "60"},
        )

    @staticmethod
    def _project_summary(
        aggregate: WeeklyReportAggregate, project: Project
    ) -> WeeklyProjectSummary:
        return WeeklyProjectSummary(
            project=WeeklyProject(id=project.id, name=project.name),
            summary=aggregate.summary,
            riskCount=aggregate.riskCount,
            riskLevelCounts=aggregate.riskLevelCounts,
            sourceRevision=aggregate.sourceRevision,
        )

    @staticmethod
    def _item(item: WeeklyReportItem) -> WeeklyReportItemResponse:
        return WeeklyReportItemResponse(
            sourceMailId=item.sourceMailId,
            sourceCandidateId=item.sourceCandidateId,
            riskId=item.riskId,
            todoId=item.todoId,
            sourceRevision=item.sourceRevision,
            summary=item.summary,
            riskLevel=item.riskLevel,
            riskStatus=item.riskStatus,
            todoStatus=item.todoStatus,
            occurredAt=item.occurredAt,
        )


__all__ = [
    "WeeklyReportService",
    "invalidate_candidate",
    "invalidate_message_project",
    "invalidate_risk",
    "invalidate_week",
    "shanghai_week_start",
]
