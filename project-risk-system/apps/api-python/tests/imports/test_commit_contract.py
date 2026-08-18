from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from risk_platform.imports.commit_service import ImportCommitService, _risk_snapshot, _snapshot
from risk_platform.imports.models import ImportBatch, ImportBatchStatus
from risk_platform.imports.schemas import (
    ConfirmImportRequest,
    ImportBatchListQuery,
    ImportSourceMeta,
)
from risk_platform.projects.models import Project, ProjectRiskLevel, ProjectStatus
from risk_platform.risks.models import Risk, RiskSourceType, RiskStatus


def test_confirmation_contract_rejects_unknown_fields() -> None:
    assert ConfirmImportRequest(acknowledgeWarnings=True).acknowledgeWarnings is True
    with pytest.raises(ValidationError):
        ConfirmImportRequest.model_validate({"acknowledgeWarnings": True, "unexpected": True})


def test_history_query_bounds_match_legacy_contract() -> None:
    assert ImportBatchListQuery(page=2, pageSize=50).pageSize == 50
    with pytest.raises(ValidationError):
        ImportBatchListQuery(page=0)
    with pytest.raises(ValidationError):
        ImportBatchListQuery(pageSize=51)


def test_import_preview_contract_exposes_processing_and_nullable_source_meta() -> None:
    assert ImportBatchStatus.PROCESSING.value == "PROCESSING"
    batch = ImportBatch(sourceMeta=None)
    assert ImportCommitService._source_meta(batch) is None
    source = ImportSourceMeta(
        sheetNames=["数据回款"],
        ignoredSheets=["汇总"],
        monthAttributes={"1": "春节", "2": None},
    )
    batch.sourceMeta = source.model_dump()
    assert ImportCommitService._source_meta(batch) == source


def test_project_snapshot_keeps_amount_precision_and_version() -> None:
    project = Project(
        id=uuid4(),
        externalCode="P-1",
        importKey="import-key",
        name="项目",
        status=ProjectStatus.DELIVERY,
        annualPlanAmount=Decimal("100.00"),
        actualCollectedAmount=Decimal("40.50"),
        remainingAmount=Decimal("59.50"),
        collectionRiskLevel=ProjectRiskLevel.HIGH,
        sourceVersion=3,
        lastImportedAt=datetime(2026, 8, 11, tzinfo=UTC),
    )
    snapshot = _snapshot(project)
    assert snapshot["annualPlanAmount"] == "100.00"
    assert snapshot["actualCollectedAmount"] == "40.50"
    assert snapshot["sourceVersion"] == 3


def test_risk_snapshot_captures_restore_identity_and_lifecycle() -> None:
    risk = Risk(
        id=uuid4(),
        projectId=uuid4(),
        categoryId=uuid4(),
        title="回款风险",
        description="需要跟进",
        level=ProjectRiskLevel.HIGH,
        status=RiskStatus.ACTIVE,
        sourceType=RiskSourceType.EXCEL,
        dedupeFingerprint="f" * 64,
        detectedAt=datetime(2026, 8, 11, tzinfo=UTC),
    )
    snapshot = _risk_snapshot(risk)
    assert snapshot["projectId"] == str(risk.projectId)
    assert snapshot["status"] == "ACTIVE"
    assert snapshot["sourceType"] == "EXCEL"


def test_restore_project_reinstates_snapshot_values() -> None:
    project = Project(
        id=uuid4(),
        name="当前名称",
        status=ProjectStatus.DELIVERY,
        collectionRiskLevel=ProjectRiskLevel.UNKNOWN,
        sourceVersion=9,
    )
    ImportCommitService._restore_project(
        project,
        {
            "externalCode": "P-1",
            "importKey": "key",
            "name": "导入前名称",
            "status": "COMPLETED",
            "collectionRiskLevel": "HIGH",
            "sourceVersion": 4,
            "annualPlanAmount": "100.00",
            "actualCollectedAmount": None,
            "remainingAmount": "100.00",
        },
    )
    assert project.name == "导入前名称"
    assert project.status is ProjectStatus.COMPLETED
    assert project.collectionRiskLevel is ProjectRiskLevel.HIGH
    assert project.sourceVersion == 4
    assert project.annualPlanAmount == Decimal("100.00")
