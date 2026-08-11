"""Durable worker handler for workbook parsing and row persistence."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.imports.models import (
    ImportBatch,
    ImportRowAction,
    ImportRowStatus,
    LegalMatterMatchStatus,
    LegalMatterRow,
    ProjectImportRow,
    ProjectRiskLevel,
    SupplementalCollectionRow,
    SupplementalMatchStatus,
)
from risk_platform.imports.parser import (
    ParsedLegalRow,
    ParsedRow,
    ParsedSupplementalRow,
    ProjectListParser,
)
from risk_platform.imports.storage import WorkbookStorage
from risk_platform.model_types import JSONValue


def _json(value: object) -> JSONValue:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json(getattr(value, key)) for key in value.__dataclass_fields__}
    return cast(JSONValue, value)


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


class ImportPreviewWorker:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], storage_root: str
    ) -> None:
        self._session_factory = session_factory
        self._storage = WorkbookStorage(__import__("pathlib").Path(storage_root))

    async def handle(self, payload: Mapping[str, object]) -> None:
        batch_id = UUID(str(payload["batch_id"]))
        try:
            async with self._session_factory() as session:
                batch = await session.scalar(select(ImportBatch).where(ImportBatch.id == batch_id))
                if batch is None:
                    raise ValueError("导入批次不存在")
                parsed = ProjectListParser().parse(self._storage.read(batch.storageKey))
        except Exception:
            async with self._session_factory() as session, session.begin():
                batch = await session.scalar(
                    select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
                )
                if batch is not None:
                    batch.status = "FAILED"
                    batch.errorRows = max(batch.errorRows, 1)
            raise
        async with self._session_factory() as session, session.begin():
            batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
            )
            if batch is None:
                raise ValueError("导入批次不存在")
            await session.execute(
                delete(ProjectImportRow).where(ProjectImportRow.batchId == batch.id)
            )
            await session.execute(
                delete(SupplementalCollectionRow).where(
                    SupplementalCollectionRow.batchId == batch.id
                )
            )
            await session.execute(delete(LegalMatterRow).where(LegalMatterRow.batchId == batch.id))
            for project_row in parsed.rows:
                session.add(self._project_row(batch.id, project_row))
            for supplemental_row in parsed.supplemental_rows:
                session.add(self._supplemental_row(batch.id, supplemental_row))
            for legal_row in parsed.legal_rows:
                session.add(self._legal_row(batch.id, legal_row))
            batch.sourceMeta = _json(
                {
                    "sheetNames": parsed.sheet_names,
                    "ignoredSheets": parsed.ignored_sheets,
                    "monthAttributes": parsed.month_attributes,
                }
            )
            batch.totalRows = len(parsed.rows)
            batch.readyRows = sum(row.status == "READY" for row in parsed.rows)
            batch.warningRows = sum(row.status == "WARNING" for row in parsed.rows)
            batch.errorRows = sum(row.status == "ERROR" for row in parsed.rows)
            batch.supplementalTotalRows = len(parsed.supplemental_rows)
            batch.supplementalWarningRows = sum(
                row.status == "WARNING" for row in parsed.supplemental_rows
            )
            batch.supplementalErrorRows = sum(
                row.status == "ERROR" for row in parsed.supplemental_rows
            )
            batch.legalTotalRows = len(parsed.legal_rows)
            batch.legalWarningRows = sum(row.status == "WARNING" for row in parsed.legal_rows)
            batch.legalErrorRows = sum(row.status == "ERROR" for row in parsed.legal_rows)

    @staticmethod
    def _project_row(batch_id: UUID, row: ParsedRow) -> ProjectImportRow:
        return ProjectImportRow(
            batchId=batch_id,
            rowNumber=row.row_number,
            importKey=row.import_key,
            action=ImportRowAction(row.action),
            status=ImportRowStatus(row.status),
            externalCode=row.external_code,
            projectName=row.project_name,
            departmentName=row.department_name,
            deliveryOwnerName=row.delivery_owner_name,
            annualPlanAmount=_decimal(row.annual_plan_amount),
            actualCollectedAmount=_decimal(row.actual_collected_amount),
            remainingAmount=_decimal(row.remaining_amount),
            monthlyCollections=_json(row.monthly_collections),
            monthAttributes=row.month_attributes,
            collectionRiskLevel=ProjectRiskLevel(row.collection_risk_level),
            collectionProgress=row.collection_progress,
            sourceSnapshot=_json(row.source_snapshot),
            warnings=row.warnings,
            errors=row.errors,
        )

    @staticmethod
    def _supplemental_row(batch_id: UUID, row: ParsedSupplementalRow) -> SupplementalCollectionRow:
        return SupplementalCollectionRow(
            batchId=batch_id,
            rowNumber=row.row_number,
            sourceKey=row.source_key,
            status=ImportRowStatus(row.status),
            matchStatus=SupplementalMatchStatus(row.match_status),
            matchedImportKey=row.matched_import_key,
            projectId=UUID(row.matched_project_id) if row.matched_project_id else None,
            externalCode=row.external_code,
            projectName=row.project_name,
            contractReceivableAmount=_decimal(row.contract_receivable_amount),
            procurementContractAmount=_decimal(row.procurement_contract_amount),
            cumulativeCollectedAmount=_decimal(row.cumulative_collected_amount),
            remainingUncollectedAmount=_decimal(row.remaining_uncollected_amount),
            actualCollectedThisYear=_decimal(row.actual_collected_this_year),
            actualCollectedNetThisYear=_decimal(row.actual_collected_net_this_year),
            annualCollectionPlan=_decimal(row.annual_collection_plan),
            collectionRiskLevel=ProjectRiskLevel(row.collection_risk_level),
            monthlyCollections=_json(row.monthly_collections),
            monthAttributes=row.month_attributes,
            afterYearAmount=_decimal(row.after_year_amount),
            sourceSnapshot=_json(row.source_snapshot),
            warnings=row.warnings,
            errors=row.errors,
        )

    @staticmethod
    def _legal_row(batch_id: UUID, row: ParsedLegalRow) -> LegalMatterRow:
        return LegalMatterRow(
            batchId=batch_id,
            rowNumber=row.row_number,
            sourceKey=row.source_key,
            status=ImportRowStatus(row.status),
            matchStatus=LegalMatterMatchStatus(row.match_status),
            matchedImportKey=row.matched_import_key,
            projectId=UUID(row.matched_project_id) if row.matched_project_id else None,
            externalCode=row.external_code,
            projectName=row.project_name,
            departmentName=row.department_name,
            deliveryOwnerName=row.delivery_owner_name,
            annualPlanAmount=_decimal(row.annual_plan_amount),
            collectionRiskLevel=ProjectRiskLevel(row.collection_risk_level),
            legalProgress=row.legal_progress,
            monthlyCollections=_json(row.monthly_collections),
            monthAttributes=row.month_attributes,
            sourceSnapshot=_json(row.source_snapshot),
            warnings=row.warnings,
            errors=row.errors,
        )


__all__ = ["ImportPreviewWorker"]
