from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from risk_platform.imports.commit_service import _snapshot
from risk_platform.imports.schemas import ConfirmImportRequest, ImportBatchListQuery
from risk_platform.projects.models import Project, ProjectRiskLevel, ProjectStatus


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
