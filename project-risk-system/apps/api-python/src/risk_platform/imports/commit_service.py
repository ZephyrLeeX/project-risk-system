"""Transactional import commit and history application service."""

from __future__ import annotations

import hashlib
from builtins import list as builtin_list
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.imports.models import (
    ImportBatch,
    ImportBatchStatus,
    ImportRowStatus,
    LegalMatterRow,
    ProjectImportRow,
    ProjectRiskLevel,
    SupplementalCollectionRow,
    SupplementalMatchStatus,
)
from risk_platform.imports.schemas import (
    ImportBatchDetail,
    ImportBatchListQuery,
    ImportBatchSummary,
    ImportRowItem,
    LegalRowItem,
    PaginatedImportBatches,
    ProjectOption,
    SupplementalRowItem,
)
from risk_platform.imports.storage import WorkbookStorage
from risk_platform.model_types import JSONValue
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.projects.models import ProjectRiskLevel as ProjectDomainRiskLevel
from risk_platform.retention.service import RetentionConfigurationRepository
from risk_platform.risks.models import ProjectRiskLevel as RiskProjectRiskLevel
from risk_platform.risks.models import Risk, RiskCategory, RiskSourceType, RiskStatus
from risk_platform.risks.service import RiskCreate, RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.timeline.models import RiskTimelineEvent
from risk_platform.todos.models import ActionItem

_MATCH_WARNING = "匹配到多个主项目，需人工确认关联关系"  # noqa: RUF001
_UNMATCHED_WARNING = "未找到可精确匹配的主项目，记录将保留为待匹配且不会新增项目"  # noqa: RUF001
_UNMATCHED_BY_ADMIN = "已由管理员解除项目关联，记录保留为待匹配"  # noqa: RUF001


def _amount(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _snapshot(project: Project) -> dict[str, JSONValue]:
    return {
        "externalCode": project.externalCode,
        "importKey": project.importKey,
        "name": project.name,
        "alias": project.alias,
        "status": project.status.value,
        "departmentId": str(project.departmentId) if project.departmentId else None,
        "managerId": str(project.managerId) if project.managerId else None,
        "deliveryOwnerName": project.deliveryOwnerName,
        "annualPlanAmount": _amount(project.annualPlanAmount),
        "actualCollectedAmount": _amount(project.actualCollectedAmount),
        "remainingAmount": _amount(project.remainingAmount),
        "monthlyCollections": project.monthlyCollections,
        "monthAttributes": project.monthAttributes,
        "collectionRiskLevel": project.collectionRiskLevel.value,
        "collectionProgress": project.collectionProgress,
        "lastImportedAt": project.lastImportedAt.isoformat() if project.lastImportedAt else None,
        "sourceVersion": project.sourceVersion,
    }


def _risk_snapshot(risk: Risk) -> dict[str, JSONValue]:
    return {
        "projectId": str(risk.projectId),
        "categoryId": str(risk.categoryId),
        "title": risk.title,
        "description": risk.description,
        "evidence": risk.evidence,
        "level": risk.level.value,
        "status": risk.status.value,
        "sourceType": risk.sourceType.value,
        "sourceBatchId": str(risk.sourceBatchId) if risk.sourceBatchId else None,
        "sourceRefId": str(risk.sourceRefId) if risk.sourceRefId else None,
        "reporterUserId": str(risk.reporterUserId) if risk.reporterUserId else None,
        "reporterNameSource": risk.reporterNameSource,
        "weekCode": risk.weekCode,
        "suggestion": risk.suggestion,
        "detectedAt": risk.detectedAt.isoformat(),
        "resolvedAt": risk.resolvedAt.isoformat() if risk.resolvedAt else None,
        "resolvedById": str(risk.resolvedById) if risk.resolvedById else None,
        "resolutionReason": risk.resolutionReason,
        "dedupeFingerprint": risk.dedupeFingerprint,
    }


class ImportCommitService:
    """Own all import writes while delegating risk/todo/timeline creation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage_root: Path,
    ) -> None:
        self._session_factory = session_factory
        self._storage = WorkbookStorage(storage_root)
        self._risks = RisksService(session_factory)

    async def list(
        self, query: ImportBatchListQuery, identity: SessionIdentity
    ) -> PaginatedImportBatches:
        async with self._session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(ImportBatch)) or 0
            rows = (
                await session.execute(
                    select(ImportBatch, User.displayName)
                    .join(User, User.id == ImportBatch.uploadedById)
                    .order_by(ImportBatch.createdAt.desc(), ImportBatch.id.desc())
                    .offset((query.page - 1) * query.pageSize)
                    .limit(query.pageSize)
                )
            ).all()
        del identity  # import.manage is the explicit capability for this surface
        return PaginatedImportBatches(
            items=[self._summary(batch, name) for batch, name in rows],
            page=query.page,
            pageSize=query.pageSize,
            total=total,
        )

    async def detail(self, batch_id: UUID, identity: SessionIdentity) -> ImportBatchDetail:
        async with self._session_factory() as session:
            batch, name = await self._load_batch(session, batch_id)
            result = await self._load_detail_rows(session, batch, name)
        del identity
        return result

    async def source(self, batch_id: UUID, identity: SessionIdentity) -> tuple[str, bytes]:
        async with self._session_factory() as session:
            batch = await session.scalar(select(ImportBatch).where(ImportBatch.id == batch_id))
        if batch is None:
            raise ApiError(404, "NOT_FOUND", "导入批次不存在")
        try:
            return batch.fileName, self._storage.read(batch.storageKey)
        except (FileNotFoundError, ValueError):
            raise ApiError(404, "NOT_FOUND", "导入源文件不存在或已被清理") from None
        finally:
            del identity

    async def project_options(self, identity: SessionIdentity) -> builtin_list[ProjectOption]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Project, Department.name)
                    .outerjoin(Department, Department.id == Project.departmentId)
                    .where(Project.status != ProjectStatus.ARCHIVED)
                    .order_by(Project.name.asc())
                    .limit(500)
                )
            ).all()
        del identity
        return [
            ProjectOption(
                id=project.id,
                externalCode=project.externalCode,
                name=project.name,
                departmentName=department,
            )
            for project, department in rows
        ]

    async def match_supplemental(
        self, row_id: UUID, project_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> ImportBatchDetail:
        async with transaction(self._session_factory) as session:
            row = await session.scalar(
                select(SupplementalCollectionRow)
                .where(SupplementalCollectionRow.id == row_id)
                .with_for_update()
            )
            if row is None:
                raise ApiError(404, "NOT_FOUND", "补充回款记录不存在")
            batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.id == row.batchId).with_for_update()
            )
            project = await session.scalar(
                select(Project).where(
                    Project.id == project_id, Project.status != ProjectStatus.ARCHIVED
                )
            )
            if batch is None or row.status is not ImportRowStatus.IMPORTED:
                raise ApiError(409, "CONFLICT", "只有已导入批次的补充回款记录可以调整关联")
            if project is None:
                raise ApiError(404, "NOT_FOUND", "目标项目不存在或已归档")
            row.projectId = project.id
            row.matchedImportKey = None
            row.matchStatus = SupplementalMatchStatus.MATCHED
            row.warnings = cast(
                JSONValue,
                [
                    item
                    for item in _strings(row.warnings)
                    if item not in {_MATCH_WARNING, _UNMATCHED_WARNING, _UNMATCHED_BY_ADMIN}
                ],
            )
            await self._refresh_supplemental_counts(session, batch.id)
            await AuditService(session).record_success(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="IMPORT",
                action="SUPPLEMENTAL_COLLECTION_MATCHED",
                resource_type="SUPPLEMENTAL_COLLECTION_ROW",
                resource_id=str(row.id),
                trace_id=trace_id,
                project_id=project.id,
            )
        return await self.detail(batch.id, identity)

    async def unmatch_supplemental(
        self, row_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> ImportBatchDetail:
        async with transaction(self._session_factory) as session:
            row = await session.scalar(
                select(SupplementalCollectionRow)
                .where(SupplementalCollectionRow.id == row_id)
                .with_for_update()
            )
            if row is None:
                raise ApiError(404, "NOT_FOUND", "补充回款记录不存在")
            batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.id == row.batchId).with_for_update()
            )
            if batch is None or row.status is not ImportRowStatus.IMPORTED:
                raise ApiError(409, "CONFLICT", "只有已导入批次的补充回款记录可以调整关联")
            if row.projectId is None:
                raise ApiError(409, "CONFLICT", "该补充回款记录当前未关联项目")
            warnings = [
                item
                for item in _strings(row.warnings)
                if item not in {_MATCH_WARNING, _UNMATCHED_WARNING, _UNMATCHED_BY_ADMIN}
            ]
            warnings.append(_UNMATCHED_BY_ADMIN)
            row.projectId = None
            row.matchedImportKey = None
            row.matchStatus = SupplementalMatchStatus.UNMATCHED
            row.warnings = cast(JSONValue, warnings)
            await self._refresh_supplemental_counts(session, batch.id)
            await AuditService(session).record_success(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="IMPORT",
                action="SUPPLEMENTAL_COLLECTION_UNMATCHED",
                resource_type="SUPPLEMENTAL_COLLECTION_ROW",
                resource_id=str(row.id),
                trace_id=trace_id,
            )
        return await self.detail(batch.id, identity)

    async def confirm(
        self, batch_id: UUID, acknowledge_warnings: bool, identity: SessionIdentity, trace_id: UUID
    ) -> ImportBatchDetail:
        async with transaction(self._session_factory) as session:
            batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
            )
            if batch is None:
                raise ApiError(404, "NOT_FOUND", "导入批次不存在")
            if batch.status is ImportBatchStatus.IMPORTED:
                pass
            elif batch.status is not ImportBatchStatus.PREVIEWED:
                raise ApiError(409, "CONFLICT", "只有预检完成的批次可以确认导入")
            else:
                if batch.errorRows or batch.supplementalErrorRows or batch.legalErrorRows:
                    raise ApiError(
                        400,
                        "IMPORT_HAS_ERRORS",
                        "批次包含错误行，请修正 Excel 后重新上传",  # noqa: RUF001
                    )
                has_warning = bool(
                    batch.warningRows or batch.supplementalWarningRows or batch.legalWarningRows
                )
                if has_warning and not acknowledge_warnings:
                    raise ApiError(
                        400, "IMPORT_WARNING_CONFIRMATION_REQUIRED", "请先确认批次中的警告信息"
                    )
                rows = (
                    await session.scalars(
                        select(ProjectImportRow)
                        .where(ProjectImportRow.batchId == batch.id)
                        .order_by(ProjectImportRow.rowNumber)
                    )
                ).all()
                supplemental = (
                    await session.scalars(
                        select(SupplementalCollectionRow).where(
                            SupplementalCollectionRow.batchId == batch.id
                        )
                    )
                ).all()
                legal = (
                    await session.scalars(
                        select(LegalMatterRow).where(LegalMatterRow.batchId == batch.id)
                    )
                ).all()
                created, updated = 0, 0
                categories = {
                    code: category
                    for category in await session.scalars(
                        select(RiskCategory).where(
                            RiskCategory.code.in_(["COLLECTION", "LITIGATION"])
                        )
                    )
                    for code in [category.code]
                }
                now = datetime.now(UTC)
                for row in rows:
                    if row.status not in {ImportRowStatus.READY, ImportRowStatus.WARNING}:
                        continue
                    department = await self._department(session, row.departmentName or "未分配")
                    project = await self._project_for_row(session, row)
                    before = _snapshot(project) if project else None
                    values = self._project_values(row, department.id, now)
                    if project is None:
                        project = Project(**values)
                        session.add(project)
                        await session.flush()
                        created += 1
                    else:
                        for key, value in values.items():
                            if key not in {"importKey"}:
                                setattr(project, key, value)
                        project.sourceVersion += 1
                        updated += 1
                    row.status = ImportRowStatus.IMPORTED
                    row.committedProjectId = project.id
                    row.beforeSnapshot = cast(JSONValue, before)
                    row.afterSnapshot = cast(JSONValue, _snapshot(project))
                    if row.collectionRiskLevel is not ProjectRiskLevel.UNKNOWN:
                        category = categories.get("COLLECTION")
                        if category is None:
                            raise ApiError(
                                409,
                                "SEED_REQUIRED",
                                "风险分类基础数据缺失，请先执行数据库种子初始化",  # noqa: RUF001
                            )
                        fingerprint = hashlib.sha256(
                            f"PROJECT_COLLECTION:{project.id}".encode()
                        ).hexdigest()
                        existing_risk = await session.scalar(
                            select(Risk).where(Risk.dedupeFingerprint == fingerprint)
                        )
                        row.beforeRiskSnapshot = cast(
                            JSONValue, _risk_snapshot(existing_risk) if existing_risk else None
                        )
                        risk = await self._risks.create_in_session(
                            session,
                            RiskCreate(
                                project_id=project.id,
                                category_id=category.id,
                                title=f"{project.name}回款风险",
                                description=row.collectionProgress
                                or "项目清单标记存在回款风险，需持续跟踪回款进展。",  # noqa: RUF001
                                evidence=f"项目清单第{row.rowNumber}行，回款风险等级：{row.collectionRiskLevel.value}。",  # noqa: RUF001
                                level=RiskProjectRiskLevel(row.collectionRiskLevel.value),
                                source_type=RiskSourceType.EXCEL,
                                source_batch_id=batch.id,
                                source_ref_id=row.id,
                                reporter_user_id=UUID(identity.user.id),
                                reporter_name=identity.user.displayName,
                                dedupe_fingerprint=fingerprint,
                                suggestion="核实回款计划与实际回款差异，明确下一次跟进时间。",  # noqa: RUF001
                                actor_name=identity.user.displayName,
                            ),
                            actor_id=UUID(identity.user.id),
                            trace_id=trace_id,
                        )
                        row.committedRiskId = risk.id
                        row.afterRiskSnapshot = cast(JSONValue, _risk_snapshot(risk))
                for supplemental_row in supplemental:
                    if supplemental_row.status in {ImportRowStatus.READY, ImportRowStatus.WARNING}:
                        if supplemental_row.projectId is None and supplemental_row.matchedImportKey:
                            matched = await session.scalar(
                                select(Project).where(
                                    Project.importKey == supplemental_row.matchedImportKey
                                )
                            )
                            supplemental_row.projectId = matched.id if matched else None
                            if matched:
                                supplemental_row.matchStatus = SupplementalMatchStatus.MATCHED
                        supplemental_row.status = ImportRowStatus.IMPORTED
                for legal_row in legal:
                    if legal_row.status not in {ImportRowStatus.READY, ImportRowStatus.WARNING}:
                        continue
                    legal_row.status = ImportRowStatus.IMPORTED
                    if legal_row.projectId is None and legal_row.matchedImportKey:
                        matched = await session.scalar(
                            select(Project).where(Project.importKey == legal_row.matchedImportKey)
                        )
                        legal_row.projectId = matched.id if matched else None
                    if (
                        legal_row.projectId
                        and legal_row.collectionRiskLevel is not ProjectRiskLevel.UNKNOWN
                    ):
                        legal_project = await session.get(Project, legal_row.projectId)
                        category = categories.get("LITIGATION")
                        if category is None:
                            raise ApiError(
                                409,
                                "SEED_REQUIRED",
                                "风险分类基础数据缺失，请先执行数据库种子初始化",  # noqa: RUF001
                            )
                        fingerprint = hashlib.sha256(
                            f"LEGAL_MATTER:{legal_row.projectId}:{legal_row.sourceKey}".encode()
                        ).hexdigest()
                        existing_risk = await session.scalar(
                            select(Risk).where(Risk.dedupeFingerprint == fingerprint)
                        )
                        legal_row.beforeRiskSnapshot = cast(
                            JSONValue, _risk_snapshot(existing_risk) if existing_risk else None
                        )
                        risk = await self._risks.create_in_session(
                            session,
                            RiskCreate(
                                project_id=legal_row.projectId,
                                category_id=category.id,
                                title=(
                                    f"{legal_project.name}发函诉讼风险"
                                    if legal_project
                                    else "法务事项风险"
                                ),
                                description=legal_row.legalProgress
                                or "发函诉讼清单标记存在法务事项，需持续跟踪处理进展。",  # noqa: RUF001
                                level=RiskProjectRiskLevel(legal_row.collectionRiskLevel.value),
                                source_type=RiskSourceType.LITIGATION,
                                source_batch_id=batch.id,
                                source_ref_id=legal_row.id,
                                reporter_user_id=UUID(identity.user.id),
                                reporter_name=identity.user.displayName,
                                dedupe_fingerprint=fingerprint,
                                actor_name=identity.user.displayName,
                            ),
                            actor_id=UUID(identity.user.id),
                            trace_id=trace_id,
                        )
                        legal_row.committedRiskId = risk.id
                        legal_row.afterRiskSnapshot = cast(JSONValue, _risk_snapshot(risk))
                batch.status = ImportBatchStatus.IMPORTED
                batch.createdRows, batch.updatedRows = created, updated
                batch.confirmedById, batch.confirmedAt = UUID(identity.user.id), now
                retention = await RetentionConfigurationRepository(session).for_version(
                    batch.retentionConfigVersion
                )
                if retention is None:
                    raise ApiError(409, "RETENTION_FACT_INVALID", "导入批次留存事实无效、不能确认")
                batch.rollbackProtectedUntil = retention.rollback_protected_until(now)
                await AuditService(session).record_success(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="IMPORT",
                    action="PROJECT_IMPORT_CONFIRMED",
                    resource_type="IMPORT_BATCH",
                    resource_id=str(batch.id),
                    trace_id=trace_id,
                )
        return await self.detail(batch_id, identity)

    async def rollback(
        self, batch_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> ImportBatchDetail:
        """Restore one imported batch atomically, rejecting later overwrites."""
        user_id = UUID(identity.user.id)
        async with transaction(self._session_factory) as session:
            batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
            )
            if batch is None:
                raise ApiError(404, "NOT_FOUND", "导入批次不存在")
            if batch.status is not ImportBatchStatus.IMPORTED:
                raise ApiError(409, "CONFLICT", "只有已导入的批次可以回滚")

            project_ids = select(ProjectImportRow.committedProjectId).where(
                ProjectImportRow.batchId == batch_id,
                ProjectImportRow.status == ImportRowStatus.IMPORTED,
                ProjectImportRow.committedProjectId.is_not(None),
            )
            later_statement = (
                select(ProjectImportRow.id)
                .join(ImportBatch, ImportBatch.id == ProjectImportRow.batchId)
                .where(
                    ProjectImportRow.committedProjectId.in_(project_ids),
                    ProjectImportRow.batchId != batch_id,
                    ImportBatch.status == ImportBatchStatus.IMPORTED,
                )
                .limit(1)
            )
            if batch.confirmedAt is not None:
                later_statement = later_statement.where(ImportBatch.confirmedAt > batch.confirmedAt)
            later = await session.scalar(later_statement)
            if later is not None:
                raise ApiError(409, "CONFLICT", "该批次涉及的项目已有后续导入，不能直接回滚")  # noqa: RUF001

            rows = (
                await session.scalars(
                    select(ProjectImportRow)
                    .where(
                        ProjectImportRow.batchId == batch_id,
                        ProjectImportRow.status == ImportRowStatus.IMPORTED,
                    )
                    .order_by(ProjectImportRow.rowNumber)
                )
            ).all()
            legal_rows = (
                await session.scalars(
                    select(LegalMatterRow).where(
                        LegalMatterRow.batchId == batch_id,
                        LegalMatterRow.status == ImportRowStatus.IMPORTED,
                    )
                )
            ).all()
            risk_ids = [row.committedRiskId for row in rows if row.committedRiskId is not None]
            risk_ids.extend(
                row.committedRiskId for row in legal_rows if row.committedRiskId is not None
            )
            if risk_ids:
                await session.execute(
                    delete(RiskTimelineEvent).where(
                        (RiskTimelineEvent.sourceBatchId == batch_id)
                        | RiskTimelineEvent.riskId.in_(risk_ids)
                    )
                )
            else:
                await session.execute(
                    delete(RiskTimelineEvent).where(RiskTimelineEvent.sourceBatchId == batch_id)
                )
            for legal_row in legal_rows:
                if legal_row.committedRiskId:
                    await self._restore_risk(
                        session, legal_row.committedRiskId, legal_row.beforeRiskSnapshot
                    )
                legal_row.status = ImportRowStatus.ROLLED_BACK
            for project_row in rows:
                if project_row.committedRiskId:
                    await self._restore_risk(
                        session, project_row.committedRiskId, project_row.beforeRiskSnapshot
                    )
                project = (
                    await session.get(Project, project_row.committedProjectId)
                    if project_row.committedProjectId
                    else None
                )
                before = project_row.beforeSnapshot
                if project is None and before is not None:
                    raise ApiError(409, "CONFLICT", "导入项目已不存在，无法安全回滚")  # noqa: RUF001
                if project is not None:
                    if isinstance(before, dict):
                        self._restore_project(project, before)
                    else:
                        await session.delete(project)
                project_row.status = ImportRowStatus.ROLLED_BACK
            await session.execute(
                update(ProjectImportRow)
                .where(
                    ProjectImportRow.batchId == batch_id,
                    ProjectImportRow.status == ImportRowStatus.IMPORTED,
                )
                .values(status=ImportRowStatus.ROLLED_BACK)
            )
            await session.execute(
                update(SupplementalCollectionRow)
                .where(
                    SupplementalCollectionRow.batchId == batch_id,
                    SupplementalCollectionRow.status == ImportRowStatus.IMPORTED,
                )
                .values(status=ImportRowStatus.ROLLED_BACK)
            )
            await session.execute(
                update(LegalMatterRow)
                .where(
                    LegalMatterRow.batchId == batch_id,
                    LegalMatterRow.status == ImportRowStatus.IMPORTED,
                )
                .values(status=ImportRowStatus.ROLLED_BACK)
            )
            import_departments = (
                await session.scalars(select(Department).where(Department.code.like("IMPORT_%")))
            ).all()
            for department in import_departments:
                has_project = await session.scalar(
                    select(Project.id).where(Project.departmentId == department.id).limit(1)
                )
                has_user = await session.scalar(
                    select(User.id).where(User.departmentId == department.id).limit(1)
                )
                if has_project is None and has_user is None:
                    await session.delete(department)
            batch.status = ImportBatchStatus.ROLLED_BACK
            batch.rolledBackById, batch.rolledBackAt = user_id, datetime.now(UTC)
            batch.rollbackProtectedUntil = None
            await AuditService(session).record_success(
                actor_id=user_id,
                actor_type=AuditActorType.USER,
                module="IMPORT",
                action="PROJECT_IMPORT_ROLLED_BACK",
                resource_type="IMPORT_BATCH",
                resource_id=str(batch_id),
                trace_id=trace_id,
            )
        return await self.detail(batch_id, identity)

    @staticmethod
    def _restore_project(project: Project, snapshot: dict[str, JSONValue]) -> None:
        for key in (
            "externalCode",
            "importKey",
            "name",
            "alias",
            "departmentId",
            "managerId",
            "deliveryOwnerName",
            "monthlyCollections",
            "monthAttributes",
            "collectionProgress",
        ):
            if key in snapshot:
                setattr(project, key, snapshot[key])
        for key in ("annualPlanAmount", "actualCollectedAmount", "remainingAmount"):
            if key in snapshot:
                setattr(
                    project,
                    key,
                    Decimal(str(snapshot[key])) if snapshot[key] is not None else None,
                )
        if snapshot.get("status") is not None:
            project.status = ProjectStatus(str(snapshot["status"]))
        if snapshot.get("collectionRiskLevel") is not None:
            project.collectionRiskLevel = ProjectDomainRiskLevel(
                str(snapshot["collectionRiskLevel"])
            )
        if snapshot.get("lastImportedAt") is not None:
            project.lastImportedAt = datetime.fromisoformat(str(snapshot["lastImportedAt"]))
        source_version = snapshot.get("sourceVersion")
        if isinstance(source_version, (int, float, str)):
            project.sourceVersion = int(source_version)

    async def _restore_risk(
        self, session: AsyncSession, risk_id: UUID, snapshot_value: JSONValue | None
    ) -> None:
        risk = await session.get(Risk, risk_id, with_for_update=True)
        if risk is None:
            return
        if not isinstance(snapshot_value, dict):
            await session.delete(risk)
            return
        for key in (
            "title",
            "description",
            "evidence",
            "reporterNameSource",
            "weekCode",
            "suggestion",
            "resolutionReason",
        ):
            setattr(risk, key, snapshot_value.get(key))
        risk.projectId = UUID(str(snapshot_value["projectId"]))
        risk.categoryId = UUID(str(snapshot_value["categoryId"]))
        risk.level = RiskProjectRiskLevel(str(snapshot_value["level"]))
        risk.status = RiskStatus(str(snapshot_value["status"]))
        risk.sourceType = RiskSourceType(str(snapshot_value["sourceType"]))
        risk.sourceBatchId = (
            UUID(str(snapshot_value["sourceBatchId"]))
            if snapshot_value.get("sourceBatchId")
            else None
        )
        risk.sourceRefId = (
            UUID(str(snapshot_value["sourceRefId"])) if snapshot_value.get("sourceRefId") else None
        )
        risk.reporterUserId = (
            UUID(str(snapshot_value["reporterUserId"]))
            if snapshot_value.get("reporterUserId")
            else None
        )
        risk.detectedAt = datetime.fromisoformat(str(snapshot_value["detectedAt"]))
        risk.resolvedAt = (
            datetime.fromisoformat(str(snapshot_value["resolvedAt"]))
            if snapshot_value.get("resolvedAt")
            else None
        )
        risk.resolvedById = (
            UUID(str(snapshot_value["resolvedById"]))
            if snapshot_value.get("resolvedById")
            else None
        )
        risk.dedupeFingerprint = str(snapshot_value["dedupeFingerprint"])
        await session.flush()
        if risk.status is RiskStatus.RESOLVED:
            await session.execute(delete(ActionItem).where(ActionItem.riskId == risk.id))

    async def _department(self, session: AsyncSession, name: str) -> Department:
        code = "IMPORT_" + hashlib.sha256(name.encode()).hexdigest()[:12].upper()
        department = await session.scalar(select(Department).where(Department.code == code))
        if department is None:
            department = Department(code=code, name=name, enabled=True, sortOrder=1000)
            session.add(department)
            await session.flush()
        else:
            department.name, department.enabled = name, True
        return department

    async def _project_for_row(
        self, session: AsyncSession, row: ProjectImportRow
    ) -> Project | None:
        if row.matchedProjectId:
            project = await session.get(Project, row.matchedProjectId, with_for_update=True)
            if project is None:
                raise ApiError(
                    409,
                    "IMPORT_STALE_PREVIEW",
                    f"第{row.rowNumber}行匹配的项目已不存在，请重新预检",  # noqa: RUF001
                )
            return project
        if row.externalCode:
            return cast(
                Project | None,
                await session.scalar(
                    select(Project)
                    .where(Project.externalCode == row.externalCode)
                    .with_for_update()
                ),
            )
        return cast(
            Project | None,
            await session.scalar(
                select(Project).where(Project.importKey == row.importKey).with_for_update()
            ),
        )

    @staticmethod
    def _project_values(
        row: ProjectImportRow, department_id: UUID, now: datetime
    ) -> dict[str, object]:
        return {
            "externalCode": row.externalCode,
            "importKey": row.importKey,
            "name": row.projectName or "未命名项目",
            "status": ProjectStatus.DELIVERY,
            "departmentId": department_id,
            "deliveryOwnerName": row.deliveryOwnerName,
            "annualPlanAmount": row.annualPlanAmount,
            "actualCollectedAmount": row.actualCollectedAmount,
            "remainingAmount": row.remainingAmount,
            "monthlyCollections": row.monthlyCollections,
            "monthAttributes": row.monthAttributes,
            "collectionRiskLevel": ProjectDomainRiskLevel(row.collectionRiskLevel.value),
            "collectionProgress": row.collectionProgress,
            "lastImportedAt": now,
        }

    async def _refresh_supplemental_counts(self, session: AsyncSession, batch_id: UUID) -> None:
        counts = {
            status: count
            for status, count in (
                await session.execute(
                    select(SupplementalCollectionRow.matchStatus, func.count())
                    .where(SupplementalCollectionRow.batchId == batch_id)
                    .group_by(SupplementalCollectionRow.matchStatus)
                )
            ).all()
        }
        batch = await session.get(ImportBatch, batch_id)
        if batch is not None:
            batch.supplementalMatchedRows = counts.get(SupplementalMatchStatus.MATCHED, 0)
            batch.supplementalUnmatchedRows = counts.get(SupplementalMatchStatus.UNMATCHED, 0)
            batch.supplementalAmbiguousRows = counts.get(SupplementalMatchStatus.AMBIGUOUS, 0)

    async def _load_batch(self, session: AsyncSession, batch_id: UUID) -> tuple[ImportBatch, str]:
        row = (
            await session.execute(
                select(ImportBatch, User.displayName)
                .join(User, User.id == ImportBatch.uploadedById)
                .where(ImportBatch.id == batch_id)
            )
        ).first()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "导入批次不存在")
        return row[0], row[1]

    async def _load_detail_rows(
        self, session: AsyncSession, batch: ImportBatch, uploaded_name: str
    ) -> ImportBatchDetail:
        rows = (
            await session.scalars(
                select(ProjectImportRow)
                .where(ProjectImportRow.batchId == batch.id)
                .order_by(ProjectImportRow.rowNumber)
            )
        ).all()
        supplemental = (
            await session.scalars(
                select(SupplementalCollectionRow)
                .where(SupplementalCollectionRow.batchId == batch.id)
                .order_by(SupplementalCollectionRow.rowNumber)
            )
        ).all()
        legal = (
            await session.scalars(
                select(LegalMatterRow)
                .where(LegalMatterRow.batchId == batch.id)
                .order_by(LegalMatterRow.rowNumber)
            )
        ).all()
        projects = {
            p.id: p
            for p in (
                await session.scalars(
                    select(Project).where(
                        Project.id.in_([r.projectId for r in supplemental if r.projectId])
                    )
                )
            ).all()
        }
        departments = {
            d.id: d.name
            for d in (
                await session.scalars(
                    select(Department).where(
                        Department.id.in_(
                            [p.departmentId for p in projects.values() if p.departmentId]
                        )
                    )
                )
            ).all()
        }
        return ImportBatchDetail(
            **self._summary(batch, uploaded_name).model_dump(),
            sourceMeta=cast(dict[str, object], batch.sourceMeta or {}),
            rows=[self._row_item(row) for row in rows],
            supplementalRows=[
                self._supplemental_item(
                    row,
                    projects.get(row.projectId) if row.projectId else None,
                    departments,
                )
                for row in supplemental
            ],
            legalRows=[self._legal_item(row) for row in legal],
        )

    @staticmethod
    def _summary(batch: ImportBatch, uploaded_name: str) -> ImportBatchSummary:
        return ImportBatchSummary(
            id=batch.id,
            fileName=batch.fileName,
            fileHash=batch.fileHash,
            status=batch.status,
            sheetName=batch.sheetName,
            totalRows=batch.totalRows,
            readyRows=batch.readyRows,
            warningRows=batch.warningRows,
            errorRows=batch.errorRows,
            createdRows=batch.createdRows,
            updatedRows=batch.updatedRows,
            supplementalTotalRows=batch.supplementalTotalRows,
            supplementalMatchedRows=batch.supplementalMatchedRows,
            supplementalUnmatchedRows=batch.supplementalUnmatchedRows,
            supplementalAmbiguousRows=batch.supplementalAmbiguousRows,
            supplementalWarningRows=batch.supplementalWarningRows,
            supplementalErrorRows=batch.supplementalErrorRows,
            legalTotalRows=batch.legalTotalRows,
            legalMatchedRows=batch.legalMatchedRows,
            legalUnmatchedRows=batch.legalUnmatchedRows,
            legalAmbiguousRows=batch.legalAmbiguousRows,
            legalWarningRows=batch.legalWarningRows,
            legalErrorRows=batch.legalErrorRows,
            uploadedByName=uploaded_name,
            createdAt=batch.createdAt,
            confirmedAt=batch.confirmedAt,
            rolledBackAt=batch.rolledBackAt,
        )

    @staticmethod
    def _row_item(row: ProjectImportRow) -> ImportRowItem:
        return ImportRowItem(
            id=row.id,
            rowNumber=row.rowNumber,
            action=row.action.value,
            status=row.status.value,
            externalCode=row.externalCode,
            projectName=row.projectName,
            departmentName=row.departmentName,
            deliveryOwnerName=row.deliveryOwnerName,
            annualPlanAmount=_amount(row.annualPlanAmount),
            actualCollectedAmount=_amount(row.actualCollectedAmount),
            remainingAmount=_amount(row.remainingAmount),
            collectionRiskLevel=row.collectionRiskLevel.value,
            collectionProgress=row.collectionProgress,
            warnings=_strings(row.warnings),
            errors=_strings(row.errors),
            matchedProjectId=row.matchedProjectId,
            committedProjectId=row.committedProjectId,
        )

    @staticmethod
    def _supplemental_item(
        row: SupplementalCollectionRow, project: Project | None, departments: dict[UUID, str]
    ) -> SupplementalRowItem:
        option = (
            ProjectOption(
                id=project.id,
                externalCode=project.externalCode,
                name=project.name,
                departmentName=(
                    departments.get(project.departmentId)
                    if project and project.departmentId
                    else None
                ),
            )
            if project
            else None
        )
        return SupplementalRowItem(
            id=row.id,
            rowNumber=row.rowNumber,
            status=row.status.value,
            matchStatus=row.matchStatus.value,
            projectId=row.projectId,
            matchedProject=option,
            externalCode=row.externalCode,
            projectName=row.projectName,
            contractReceivableAmount=_amount(row.contractReceivableAmount),
            procurementContractAmount=_amount(row.procurementContractAmount),
            cumulativeCollectedAmount=_amount(row.cumulativeCollectedAmount),
            remainingUncollectedAmount=_amount(row.remainingUncollectedAmount),
            actualCollectedThisYear=_amount(row.actualCollectedThisYear),
            actualCollectedNetThisYear=_amount(row.actualCollectedNetThisYear),
            annualCollectionPlan=_amount(row.annualCollectionPlan),
            collectionRiskLevel=row.collectionRiskLevel.value,
            afterYearAmount=_amount(row.afterYearAmount),
            warnings=_strings(row.warnings),
            errors=_strings(row.errors),
        )

    @staticmethod
    def _legal_item(row: LegalMatterRow) -> LegalRowItem:
        return LegalRowItem(
            id=row.id,
            rowNumber=row.rowNumber,
            status=row.status.value,
            matchStatus=row.matchStatus.value,
            projectId=row.projectId,
            externalCode=row.externalCode,
            projectName=row.projectName,
            departmentName=row.departmentName,
            deliveryOwnerName=row.deliveryOwnerName,
            annualPlanAmount=_amount(row.annualPlanAmount),
            collectionRiskLevel=row.collectionRiskLevel.value,
            legalProgress=row.legalProgress,
            warnings=_strings(row.warnings),
            errors=_strings(row.errors),
        )


__all__ = ["ImportCommitService"]
