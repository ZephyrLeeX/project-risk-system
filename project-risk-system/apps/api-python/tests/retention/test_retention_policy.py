from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from risk_platform.imports.models import ImportBatch
from risk_platform.retention.configuration import FrozenRetentionConfiguration, RetentionSettings
from risk_platform.retention.service import RetentionDecision, RetentionProtectionService


def _batch(**changes: object) -> ImportBatch:
    facts: dict[str, object] = {
        "sourceExpiresAt": datetime(2026, 8, 12, tzinfo=UTC),
        "rollbackProtectedUntil": None,
        "retentionConfigVersion": "V12.4",
    }
    facts.update(changes)
    return cast(ImportBatch, SimpleNamespace(**facts))


def test_retention_settings_are_strict_and_bounded() -> None:
    assert RetentionSettings().model_dump() == {
        "importSourceRetentionDays": 365,
        "agentConversationRetentionDays": 90,
        "importRollbackProtectionDays": 30,
    }
    for field, value in (
        ("importSourceRetentionDays", 29),
        ("agentConversationRetentionDays", 366),
        ("importRollbackProtectionDays", "30"),
    ):
        with pytest.raises(ValidationError):
            RetentionSettings.model_validate({field: value})


def test_frozen_expirations_use_utc_day_boundaries() -> None:
    configuration = FrozenRetentionConfiguration("V12.4", RetentionSettings())
    created = datetime(2026, 1, 1, 3, 4, 5, tzinfo=UTC)
    assert configuration.source_expires_at(created) == created + timedelta(days=365)
    assert configuration.conversation_expires_at(created) == created + timedelta(days=90)
    assert configuration.rollback_protected_until(created) == created + timedelta(days=30)


def test_import_protection_order_is_closed_and_boundary_safe() -> None:
    as_of = datetime(2026, 8, 12, tzinfo=UTC)
    assert (
        RetentionProtectionService.import_batch(
            _batch(sourceExpiresAt=None),
            retention_config_known=True,
            has_active_hold=True,
            active_operation=True,
            as_of=as_of,
        ).decision
        is RetentionDecision.MISSING_RETENTION_FACT
    )
    assert (
        RetentionProtectionService.import_batch(
            _batch(rollbackProtectedUntil=as_of + timedelta(seconds=1)),
            retention_config_known=True,
            has_active_hold=True,
            active_operation=True,
            as_of=as_of,
        ).decision
        is RetentionDecision.ACTIVE_AUDIT_HOLD
    )
    assert (
        RetentionProtectionService.import_batch(
            _batch(rollbackProtectedUntil=as_of + timedelta(seconds=1)),
            retention_config_known=True,
            has_active_hold=False,
            active_operation=True,
            as_of=as_of,
        ).decision
        is RetentionDecision.ROLLBACK_WINDOW
    )
    assert (
        RetentionProtectionService.import_batch(
            _batch(sourceExpiresAt=as_of + timedelta(seconds=1)),
            retention_config_known=True,
            has_active_hold=False,
            active_operation=True,
            as_of=as_of,
        ).decision
        is RetentionDecision.ACTIVE_OPERATION
    )
    assert (
        RetentionProtectionService.import_batch(
            _batch(sourceExpiresAt=as_of + timedelta(seconds=1)),
            retention_config_known=True,
            has_active_hold=False,
            active_operation=False,
            as_of=as_of,
        ).decision
        is RetentionDecision.RETENTION_NOT_DUE
    )
    assert (
        RetentionProtectionService.import_batch(
            _batch(sourceExpiresAt=as_of, rollbackProtectedUntil=as_of),
            retention_config_known=True,
            has_active_hold=False,
            active_operation=False,
            as_of=as_of,
        ).decision
        is RetentionDecision.ELIGIBLE
    )


def test_unknown_config_version_is_fail_closed() -> None:
    result = RetentionProtectionService.conversation(
        expires_at=datetime(2026, 8, 12, tzinfo=UTC),
        retention_config_version="missing",
        retention_config_known=False,
        has_active_hold=False,
        active_operation=False,
        as_of=datetime(2026, 8, 12, tzinfo=UTC),
    )
    assert result.decision is RetentionDecision.MISSING_RETENTION_FACT
