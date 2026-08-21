"""Dashboard aggregates built only from the caller's scoped projects."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from risk_platform.admin.models import Department
from risk_platform.auth.service import SessionIdentity
from risk_platform.imports.models import (
    ImportBatch,
    ImportBatchStatus,
    ImportRowStatus,
    SupplementalCollectionRow,
    SupplementalMatchStatus,
)
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.risks.models import (
    ProjectRiskLevel,
    Risk,
    RiskCategory,
    RiskSourceType,
    RiskStatus,
)
from risk_platform.risks.schemas import RiskItem, RiskQuery
from risk_platform.risks.service import RisksService
from risk_platform.shared.time_ranges import current_week_start

from .schemas import (
    ActiveCollectionRisk,
    CollectionProjectItem,
    CollectionQuery,
    CollectionTotals,
    DashboardSummary,
    DepartmentCollectionDetail,
    DepartmentCollectionItem,
    DepartmentCollectionSummary,
    NextCollectionInfo,
    RiskCollectionDetail,
    RiskCollectionListResponse,
    RiskCollectionMonthItem,
    RiskCollectionProjectItem,
)


class DashboardService:
    """Own dashboard-only read aggregates; risk rendering stays in RisksService."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._risks = RisksService(session_factory)

    @staticmethod
    def _scope(identity: SessionIdentity) -> ColumnElement[bool]:
        return project_scope_predicate(
            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
        )

    async def focus(self, identity: SessionIdentity) -> list[RiskItem]:
        # The legacy endpoint chooses active risks by severity, remaining amount and recency.
        page = await self._risks.list(identity, RiskQuery(page=1, pageSize=100))
        assert not isinstance(page, tuple)
        return sorted(
            page.items,
            key=lambda item: (
                {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}[item.level.value],
                -Decimal(item.remainingAmountYuan or "0"),
                item.updatedAt,
            ),
        )[:5]

    async def summary(self, identity: SessionIdentity) -> DashboardSummary:
        scope = self._scope(identity)
        now = datetime.now(UTC)
        # "本周新增" boundary: the shared Asia/Shanghai Monday-00:00 authority
        # (``shared.time_ranges``), the same instant the Agent risk_list
        # CURRENT_WEEK preset resolves to — previously this was a UTC-midnight
        # Monday, which disagreed with the weekly-report week convention by
        # eight hours and with the Agent time-range presets.
        week_start = current_week_start(now)
        async with self._session_factory() as session:
            project_stats = (
                await session.execute(
                    select(
                        func.count(Project.id),
                        func.count(Project.id).filter(Project.status == ProjectStatus.DELIVERY),
                        func.count(func.distinct(Project.departmentId)).filter(
                            Project.status == ProjectStatus.DELIVERY,
                            Project.departmentId.is_not(None),
                        ),
                        func.max(Project.lastImportedAt),
                    ).where(scope)
                )
            ).one()
            active_conditions = [scope, Risk.status == RiskStatus.ACTIVE]
            risk_stats = (
                await session.execute(
                    select(
                        func.count(Risk.id),
                        func.count(Risk.id).filter(Risk.level == ProjectRiskLevel.HIGH),
                        func.count(Risk.id).filter(Risk.level == ProjectRiskLevel.MEDIUM),
                        func.count(Risk.id).filter(Risk.level == ProjectRiskLevel.LOW),
                        func.count(Risk.id).filter(Risk.level == ProjectRiskLevel.UNKNOWN),
                        func.count(func.distinct(Risk.projectId)),
                        func.count(func.distinct(Risk.projectId)).filter(
                            Risk.level == ProjectRiskLevel.HIGH
                        ),
                        func.count(Risk.id).filter(Risk.detectedAt >= week_start),
                        func.count(Risk.id).filter(
                            Risk.level == ProjectRiskLevel.HIGH,
                            Risk.detectedAt >= week_start,
                        ),
                        func.count(Risk.id).filter(Risk.sourceType == RiskSourceType.MAIL_AI),
                        func.count(Risk.id).filter(Risk.sourceType == RiskSourceType.MANUAL),
                        func.count(Risk.id).filter(Risk.sourceType == RiskSourceType.EXCEL),
                        func.count(Risk.id).filter(Risk.sourceType == RiskSourceType.LITIGATION),
                        func.max(Risk.updatedAt),
                    )
                    .select_from(Risk)
                    .join(Project, Project.id == Risk.projectId)
                    .where(*active_conditions)
                )
            ).one()
            active_project_ids = (
                select(Risk.projectId)
                .join(Project, Project.id == Risk.projectId)
                .where(*active_conditions)
                .distinct()
                .subquery()
            )
            amount_stats = (
                await session.execute(
                    select(
                        func.count(Project.id).filter(
                            Project.actualCollectedAmount.is_not(None),
                            Project.remainingAmount.is_not(None),
                        ),
                        func.coalesce(
                            func.sum(Project.actualCollectedAmount).filter(
                                Project.actualCollectedAmount.is_not(None),
                                Project.remainingAmount.is_not(None),
                            ),
                            Decimal(0),
                        ),
                        func.coalesce(
                            func.sum(Project.remainingAmount).filter(
                                Project.actualCollectedAmount.is_not(None),
                                Project.remainingAmount.is_not(None),
                            ),
                            Decimal(0),
                        ),
                    )
                    .join(active_project_ids, active_project_ids.c.projectId == Project.id)
                    .where(scope)
                )
            ).one()
            high_project_rows = (
                await session.execute(
                    select(Project.name, func.max(Risk.updatedAt).label("latest"))
                    .select_from(Risk)
                    .join(Project, Project.id == Risk.projectId)
                    .where(*active_conditions, Risk.level == ProjectRiskLevel.HIGH)
                    .group_by(Project.id, Project.name)
                    .order_by(func.max(Risk.updatedAt).desc())
                    .limit(2)
                )
            ).all()
            priority_rows = (
                await session.execute(
                    select(
                        func.coalesce(func.nullif(func.trim(Risk.suggestion), ""), Risk.title)
                    )
                    .select_from(Risk)
                    .join(Project, Project.id == Risk.projectId)
                    .where(*active_conditions, Risk.level == ProjectRiskLevel.HIGH)
                    .order_by(Risk.updatedAt.desc())
                    .limit(3)
                )
            ).all()
        project_total, delivery_total, delivery_departments, latest_project = project_stats
        (
            active_total,
            high_total,
            medium_total,
            low_total,
            unknown_total,
            risk_project_total,
            high_project_total,
            weekly_new_total,
            weekly_new_high_total,
            mail_ai_total,
            manual_total,
            excel_total,
            litigation_total,
            latest_risk,
        ) = risk_stats
        complete_count, collected, remaining = amount_stats
        collected = collected or Decimal(0)
        remaining = remaining or Decimal(0)
        denominator = collected + remaining
        latest = max((value for value in (latest_project, latest_risk) if value), default=None)
        high_project_names = [name for name, _latest in high_project_rows]
        priorities = list(dict.fromkeys(str(value).strip() for value, in priority_rows))[:3]
        return DashboardSummary(
            projectTotal=project_total,
            deliveryProjectTotal=delivery_total,
            deliveryDepartmentTotal=delivery_departments,
            latestImportBatchCode=None,
            latestImportCreatedProjectTotal=0,
            activeRiskTotal=active_total,
            highRiskTotal=high_total,
            mediumRiskTotal=medium_total,
            lowRiskTotal=low_total,
            unknownRiskTotal=unknown_total,
            riskProjectTotal=risk_project_total,
            highRiskProjectTotal=high_project_total,
            weeklyNewRiskTotal=weekly_new_total,
            weeklyNewHighRiskTotal=weekly_new_high_total,
            mailAiRiskTotal=mail_ai_total,
            manualRiskTotal=manual_total,
            excelRiskTotal=excel_total,
            litigationRiskTotal=litigation_total,
            highRiskFocusProjectNames=high_project_names,
            highRiskPriorityItems=priorities,
            riskRemainingAmountYuan=f"{remaining:.2f}" if complete_count else None,
            riskCollectedAmountYuan=f"{collected:.2f}" if complete_count else None,
            riskAmountCompleteProjectTotal=complete_count,
            riskAmountMissingProjectTotal=risk_project_total - complete_count,
            riskCollectionCompletionRate=(
                float((collected / denominator * 100).quantize(Decimal("0.1")))
                if denominator > 0
                else None
            ),
            updatedAt=latest.isoformat().replace("+00:00", "Z") if latest else None,
            dataScope=DataScopeType(identity.user.dataScope),
        )

    async def department_collections(
        self, identity: SessionIdentity
    ) -> DepartmentCollectionSummary:
        views, pending = await self._collection_views(identity)
        grouped: dict[str, list[_CollectionView]] = defaultdict(list)
        for view in views:
            grouped[str(view.department.id) if view.department else "unassigned"].append(view)
        items = [
            DepartmentCollectionItem(
                departmentId=projects[0].department.id if projects[0].department else None,
                departmentKey=key,
                departmentName=projects[0].department.name
                if projects[0].department
                else "未分配部门",
                **self._totals(projects).model_dump(),
            )
            for key, projects in grouped.items()
        ]
        items.sort(
            key=lambda item: (
                -(Decimal(item.remainingAmountYuan) if item.remainingAmountYuan else -1),
                item.departmentName,
            )
        )
        pending_count, pending_amount = pending
        return DepartmentCollectionSummary(
            items=items,
            totals=self._totals(views),
            pendingSupplementalCount=pending_count,
            pendingSupplementalReceivableAmountYuan=_amount(pending_amount),
            updatedAt=_latest(view.updated_at for view in views),
            dataScope=DataScopeType(identity.user.dataScope),
        )

    async def department_collection_detail(
        self, identity: SessionIdentity, department_key: str
    ) -> DepartmentCollectionDetail:
        views, _pending = await self._collection_views(identity)
        matching = [
            view
            for view in views
            if (department_key == "unassigned" and view.department is None)
            or (view.department is not None and str(view.department.id) == department_key)
        ]
        if not matching:
            from risk_platform.shared.errors import ApiError

            raise ApiError(404, "NOT_FOUND", "部门不存在或不在当前数据范围内")
        matching.sort(key=lambda view: (-(view.remaining or Decimal(-1)), view.project.name))
        first = matching[0]
        return DepartmentCollectionDetail(
            departmentId=first.department.id if first.department else None,
            departmentKey=department_key,
            departmentName=first.department.name if first.department else "未分配部门",
            summary=self._totals(matching),
            projects=[view.item for view in matching],
            updatedAt=_latest(view.updated_at for view in matching),
        )

    async def risk_collections(
        self, identity: SessionIdentity, query: CollectionQuery
    ) -> RiskCollectionListResponse:
        views, _pending = await self._collection_views(identity)
        if query.keyword and query.keyword.strip():
            keyword = query.keyword.strip().lower()
            views = [
                view
                for view in views
                if keyword in view.project.name.lower()
                or keyword in (view.project.externalCode or "").lower()
                or keyword in (view.project.collectionProgress or "").lower()
            ]
        if query.owner and query.owner.strip():
            views = [
                view for view in views if view.project.deliveryOwnerName == query.owner.strip()
            ]
        risk_views = [view for view in views if view.risks]
        if query.level:
            risk_views = [
                view
                for view in risk_views
                if any(risk.level == query.level for risk, _category in view.risks)
            ]
        items = [self._risk_collection_item(view) for view in risk_views]
        items.sort(
            key=lambda item: (
                _risk_order(item.riskLevel),
                -(Decimal(item.remainingAmountYuan) if item.remainingAmountYuan else -1),
                item.projectName,
            )
        )
        owners = sorted(
            {
                view.project.deliveryOwnerName
                for view in risk_views
                if view.project.deliveryOwnerName
            }
        )
        return RiskCollectionListResponse(
            items=items,
            totals=self._totals(risk_views),
            riskProjectTotal=len(risk_views),
            owners=owners,
            updatedAt=_latest(view.risk_updated_at for view in risk_views),
            dataScope=DataScopeType(identity.user.dataScope),
        )

    async def risk_collection_detail(
        self, identity: SessionIdentity, project_id: UUID
    ) -> RiskCollectionDetail:
        views, _pending = await self._collection_views(identity)
        view = next((item for item in views if item.project.id == project_id and item.risks), None)
        if view is None:
            from risk_platform.shared.errors import ApiError

            raise ApiError(404, "NOT_FOUND", "风险项目不存在或不在当前数据范围内")
        item = self._risk_collection_item(view)
        monthly = _monthly(
            view.supplemental if item.amountSource == "SUPPLEMENTAL" else [view.project]
        )
        return RiskCollectionDetail(
            **item.model_dump(),
            monthlyCollections=[
                RiskCollectionMonthItem(month=m, attribute=a, amountYuan=_amount(v))
                for m, a, v in monthly
            ],
            activeRisks=[
                ActiveCollectionRisk(
                    id=risk.id,
                    title=risk.title,
                    description=risk.description,
                    level=risk.level.value,
                    categoryName=category.name,
                    sourceLabel=_source_label(risk.sourceType.value),
                    detectedAt=_iso(risk.detectedAt) or "",
                )
                for risk, category in view.risks
            ],
            statisticalScope="仅统计当前存在有效风险的项目；Excel 空金额不按 0 计算，已确认关联的涵谷回款可作为补充金额来源。",  # noqa: E501
        )

    async def _collection_views(
        self, identity: SessionIdentity
    ) -> tuple[list[_CollectionView], tuple[int | None, Decimal | None]]:
        scope = self._scope(identity)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Project, Department)
                    .outerjoin(Department, Department.id == Project.departmentId)
                    .where(scope)
                )
            ).all()
            project_ids = [project.id for project, _department in rows]
            supplemental_rows = (
                (
                    await session.execute(
                        select(SupplementalCollectionRow)
                        .join(ImportBatch, ImportBatch.id == SupplementalCollectionRow.batchId)
                        .where(
                            SupplementalCollectionRow.projectId.in_(project_ids),
                            SupplementalCollectionRow.status == ImportRowStatus.IMPORTED,
                            SupplementalCollectionRow.matchStatus
                            == SupplementalMatchStatus.MATCHED,
                            ImportBatch.status == ImportBatchStatus.IMPORTED,
                        )
                    )
                )
                .scalars()
                .all()
                if project_ids
                else []
            )
            risks = (
                (
                    await session.execute(
                        select(Risk, RiskCategory)
                        .join(RiskCategory, RiskCategory.id == Risk.categoryId)
                        .where(Risk.projectId.in_(project_ids), Risk.status == RiskStatus.ACTIVE)
                        .order_by(Risk.level, Risk.detectedAt.desc())
                    )
                ).all()
                if project_ids
                else []
            )
            can_manage = "admin.import.manage" in identity.user.permissions
            pending_rows = (
                (
                    await session.execute(
                        select(SupplementalCollectionRow.contractReceivableAmount)
                        .join(ImportBatch, ImportBatch.id == SupplementalCollectionRow.batchId)
                        .where(
                            SupplementalCollectionRow.status == ImportRowStatus.IMPORTED,
                            SupplementalCollectionRow.matchStatus.in_(
                                (
                                    SupplementalMatchStatus.UNMATCHED,
                                    SupplementalMatchStatus.AMBIGUOUS,
                                )
                            ),
                            ImportBatch.status == ImportBatchStatus.IMPORTED,
                        )
                    )
                )
                .scalars()
                .all()
                if can_manage
                else []
            )
        supplemental_by_project: dict[UUID, list[SupplementalCollectionRow]] = defaultdict(list)
        for row in supplemental_rows:
            if row.projectId is not None:
                supplemental_by_project[row.projectId].append(row)
        risks_by_project: dict[UUID, list[tuple[Risk, RiskCategory]]] = defaultdict(list)
        for risk, category in risks:
            risks_by_project[risk.projectId].append((risk, category))
        return (
            [
                self._collection_view(
                    project,
                    department,
                    supplemental_by_project[project.id],
                    risks_by_project[project.id],
                )
                for project, department in rows
            ],
            (
                (
                    len(pending_rows),
                    sum((value or Decimal(0) for value in pending_rows), Decimal(0)),
                )
                if can_manage
                else (None, None)
            ),
        )

    def _collection_view(
        self,
        project: Project,
        department: Department | None,
        supplemental: list[SupplementalCollectionRow],
        risks: list[tuple[Risk, RiskCategory]],
    ) -> _CollectionView:
        values = _resolve_amounts(project, supplemental)
        updated_at = max(
            (
                value
                for value in [project.lastImportedAt, *(row.updatedAt for row in supplemental)]
                if value is not None
            ),
            default=None,
        )
        return _CollectionView(
            project,
            department,
            supplemental,
            risks,
            updated_at,
            max((risk.updatedAt for risk, _category in risks), default=None),
            **values,
        )

    def _totals(self, views: list[_CollectionView]) -> CollectionTotals:
        complete = [view for view in views if view.complete]

        def total(field: str) -> Decimal | None:
            return (
                sum((getattr(view, field) for view in complete), Decimal(0)) if complete else None
            )

        receivable, collected, remaining = (
            total("receivable"),
            total("collected"),
            total("remaining"),
        )
        return CollectionTotals(
            projectTotal=len(views),
            amountCompleteProjectTotal=len(complete),
            amountMissingProjectTotal=len(views) - len(complete),
            receivableAmountYuan=_amount(receivable),
            collectedAmountYuan=_amount(collected),
            remainingAmountYuan=_amount(remaining),
            completionRate=_rate(receivable, collected),
        )

    def _risk_collection_item(self, view: _CollectionView) -> RiskCollectionProjectItem:
        current_month = datetime.now(UTC).month
        monthly = _monthly(
            view.supplemental if view.amount_source == "SUPPLEMENTAL" else [view.project]
        )
        next_item = _next_collection(monthly, view.project.collectionProgress, current_month)
        return RiskCollectionProjectItem(
            **view.item.model_dump(),
            departmentName=view.department.name if view.department else None,
            riskLevel=max(
                (risk.level.value for risk, _category in view.risks),
                key=_risk_order,
                default="UNKNOWN",
            ),
            activeRiskTotal=len(view.risks),
            collectionProgress=view.project.collectionProgress,
            nextCollection=next_item,
            updatedAt=_latest((view.updated_at, view.risk_updated_at)),
        )


@dataclass(frozen=True)
class _CollectionView:
    project: Project
    department: Department | None
    supplemental: list[SupplementalCollectionRow]
    risks: list[tuple[Risk, RiskCategory]]
    updated_at: datetime | None
    risk_updated_at: datetime | None
    amount_source: str
    receivable: Decimal | None
    collected: Decimal | None
    remaining: Decimal | None
    complete: bool
    supplemental_row_count: int

    @property
    def item(self) -> CollectionProjectItem:
        return CollectionProjectItem(
            projectId=self.project.id,
            externalCode=self.project.externalCode,
            projectName=self.project.name,
            ownerName=self.project.deliveryOwnerName,
            amountSource=self.amount_source,
            amountSourceLabel={
                "PROJECT_LIST": "项目清单 Excel",
                "SUPPLEMENTAL": "涵谷回款",
                "MISSING": "数据待补充",
            }[self.amount_source],
            supplementalRowCount=self.supplemental_row_count,
            receivableAmountYuan=_amount(self.receivable),
            collectedAmountYuan=_amount(self.collected),
            remainingAmountYuan=_amount(self.remaining),
            completionRate=_rate(self.receivable, self.collected),
        )


def _triple(
    receivable: Decimal | None, collected: Decimal | None, remaining: Decimal | None
) -> tuple[Decimal | None, Decimal | None, Decimal | None, bool]:
    if receivable is None and collected is not None and remaining is not None:
        receivable = collected + remaining
    if collected is None and receivable is not None and remaining is not None:
        collected = max(Decimal(0), receivable - remaining)
    if remaining is None and receivable is not None and collected is not None:
        remaining = max(Decimal(0), receivable - collected)
    return (
        receivable,
        collected,
        remaining,
        receivable is not None and collected is not None and remaining is not None,
    )


def _resolve_amounts(
    project: Project, supplemental: list[SupplementalCollectionRow]
) -> dict[str, Any]:
    receivable, collected, remaining, complete = _triple(
        project.annualPlanAmount, project.actualCollectedAmount, project.remainingAmount
    )
    if complete:
        return {
            "amount_source": "PROJECT_LIST",
            "receivable": receivable,
            "collected": collected,
            "remaining": remaining,
            "complete": True,
            "supplemental_row_count": len(supplemental),
        }
    resolved = [
        _triple(
            row.contractReceivableAmount,
            row.cumulativeCollectedAmount,
            row.remainingUncollectedAmount,
        )
        for row in supplemental
    ]
    if resolved and all(row[3] for row in resolved):
        return {
            "amount_source": "SUPPLEMENTAL",
            "receivable": sum((row[0] for row in resolved if row[0] is not None), Decimal(0)),
            "collected": sum((row[1] for row in resolved if row[1] is not None), Decimal(0)),
            "remaining": sum((row[2] for row in resolved if row[2] is not None), Decimal(0)),
            "complete": True,
            "supplemental_row_count": len(supplemental),
        }
    return {
        "amount_source": "PROJECT_LIST"
        if any(
            value is not None
            for value in (
                project.annualPlanAmount,
                project.actualCollectedAmount,
                project.remainingAmount,
            )
        )
        else ("SUPPLEMENTAL" if supplemental else "MISSING"),
        "receivable": receivable,
        "collected": collected,
        "remaining": remaining,
        "complete": False,
        "supplemental_row_count": len(supplemental),
    }


def _amount(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _rate(receivable: Decimal | None, collected: Decimal | None) -> float | None:
    if receivable is None or collected is None or receivable <= 0:
        return None
    return float((collected / receivable * 100).quantize(Decimal("0.1")))


def _latest(values: Any) -> str | None:
    latest = max((value for value in values if value is not None), default=None)
    return latest.isoformat().replace("+00:00", "Z") if latest else None


def _risk_order(level: str) -> int:
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}[level]


def _source_label(source: str) -> str:
    return {
        "MAIL_AI": "邮件 AI 识别",
        "MANUAL": "人工上报",
        "EXCEL": "Excel 导入",
        "LITIGATION": "发函/诉讼清单",
    }[source]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _monthly(records: list[Any]) -> list[tuple[int, str | None, Decimal | None]]:
    values: dict[int, tuple[str | None, Decimal | None]] = {}
    for record in records:
        raw = record.monthlyCollections or []
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("month"), int)
                or not 1 <= entry["month"] <= 12
            ):
                continue
            amount = entry.get("amount")
            try:
                parsed = Decimal(str(amount)) if amount is not None else None
            except Exception:
                parsed = None
            attribute = entry.get("attribute") if isinstance(entry.get("attribute"), str) else None
            current = values.get(entry["month"])
            previous_amount = current[1] if current else None
            values[entry["month"]] = (
                current[0] if current and current[0] else attribute,
                (previous_amount or Decimal(0)) + parsed if parsed is not None else previous_amount,
            )
    return [(month, attribute, amount) for month, (attribute, amount) in sorted(values.items())]


def _next_collection(
    monthly: list[tuple[int, str | None, Decimal | None]], progress: str | None, current_month: int
) -> NextCollectionInfo:
    planned = [
        (month, attribute, amount)
        for month, attribute, amount in monthly
        if amount is not None
        and amount > 0
        and (attribute is None or "预计" in attribute or "计划" in attribute)
    ]
    if planned:
        month, attribute, amount = next(
            (item for item in planned if item[0] >= current_month), planned[0]
        )
        return NextCollectionInfo(
            source="MONTHLY_PLAN",
            month=month,
            attribute=attribute,
            amountYuan=_amount(amount),
            label=f"{month}月 · {attribute or '计划回款'}",
        )
    if progress and progress.strip():
        return NextCollectionInfo(
            source="PROGRESS_TEXT",
            month=None,
            attribute=None,
            amountYuan=None,
            label=progress.strip(),
        )
    return NextCollectionInfo(
        source="MISSING", month=None, attribute=None, amountYuan=None, label="待补充回款节点"
    )
