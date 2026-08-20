from __future__ import annotations

import io
import os
import zipfile
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from risk_platform.audit.http import _filter_conditions, _render_export
from risk_platform.audit.schemas import (
    AuditActionGroup,
    AuditDateRange,
    AuditExportFormat,
    AuditExportRequest,
    AuditFilter,
    AuditLogListItem,
)
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity


def _item() -> AuditLogListItem:
    return AuditLogListItem(
        id=uuid4(),
        eventId="AUD-20260811-120000-ABCDEF",
        createdAt=datetime.now(UTC),
        actorName="管理员",
        actorAccount="admin",
        actorRole="系统管理员",
        module="AUDIT",  # type: ignore[arg-type]
        moduleLabel="审计",
        rawModule="AUDIT",
        action="AUDIT_LOG_EXPORTED",
        actionLabel="导出",
        actionGroup=AuditActionGroup.EXPORT,
        resourceType="AUDIT_EXPORT",
        resourceId=None,
        resourceLabel="AUDIT_EXPORT",
        summary="导出 · AUDIT_EXPORT",
        result="SUCCESS",
        traceId=str(uuid4()),
        errorCode=None,
    )


def test_audit_requests_are_closed_and_export_reason_is_required() -> None:
    with pytest.raises(ValidationError):
        AuditExportRequest.model_validate(
            {"format": AuditExportFormat.CSV, "reason": "okay", "payload": {}}
        )
    with pytest.raises(ValidationError):
        AuditExportRequest(format=AuditExportFormat.CSV, reason="no")
    query = AuditFilter(
        dateRange=AuditDateRange.CUSTOM,
        startDate=date(2026, 8, 1),
        endDate=date(2026, 8, 2),
    )
    assert len(_filter_conditions(query)) == 2


def test_export_formats_are_metadata_only_and_readable() -> None:
    item = _item()
    csv_file = _render_export([item], AuditExportFormat.CSV, "审计核查")
    assert csv_file.media_type.startswith("text/csv")
    assert b"password" not in csv_file.content.lower()
    assert "AUD-20260811-120000-ABCDEF" in csv_file.content.decode("utf-8-sig")

    xlsx_file = _render_export([item], AuditExportFormat.XLSX, "审计核查")
    assert xlsx_file.media_type.startswith("application/vnd.openxmlformats")
    with zipfile.ZipFile(io.BytesIO(xlsx_file.content)) as archive:
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "AUDIT_LOG_EXPORTED" not in worksheet
    assert "审计" in worksheet


def test_postgresql_validation_is_skipped_without_test_database_url() -> None:
    if os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL 已配置；PostgreSQL 集成由数据库专项测试执行")  # noqa: RUF001
    pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL audit query validation 未执行")


def test_audit_identity_contract_is_explicit() -> None:
    identity = SessionIdentity(
        session_id=uuid4(),
        expires_at=datetime.now(UTC),
        user=AuthenticatedUser(
            id=str(uuid4()),
            username="auditor",
            displayName="审计员",
            departmentName=None,
            roleCodes=["VIEWER_AUDITOR"],
            permissions=["admin.audit.view"],
            dataScope="ASSIGNED",
            mustChangePassword=False,
        ),
    )
    assert identity.user.dataScope == "ASSIGNED"
