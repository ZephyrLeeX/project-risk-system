from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from risk_platform.mailbox.models import (
    MailMessage,
    MailMessageProjectMatch,
    MailMessageStatus,
    MailProjectMatchType,
    MailRiskCandidate,
    MailRiskCandidateStatus,
    ProjectRiskLevel,
)
from risk_platform.mailbox.sync_results.service import MailSyncResultsService


def test_detail_maps_parser_attachment_metadata_and_multiple_projects() -> None:
    project_a = uuid4()
    project_b = uuid4()
    category = uuid4()

    message = cast(
        "MailMessage",
        SimpleNamespace(
            id=uuid4(),
            batchId=uuid4(),
            status=MailMessageStatus.COMPLETED,
            subject="项目周报",
            senderName="Sender",
            senderAddress="sender@example.com",
            sentAt=None,
            processedAt=None,
            projectResolutionCandidates=None,
            projectResolutionStatus=None,
            failureSummary=None,
            keyPoints=["关键要点"],
            sanitizedSummary="周报摘要",
            attachmentMetadata=[
                {
                    "name": "weekly.xlsx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "extension": ".xlsx",
                    "sizeBytes": 1024,
                    "allowedFormat": "XLSX",
                    "status": "PARSED",
                    "code": None,
                },
                {
                    "name": "payload.exe",
                    "mimeType": "application/octet-stream",
                    "extension": ".exe",
                    "sizeBytes": 2048,
                    "allowedFormat": None,
                    "status": "UNSUPPORTED",
                    "code": "UNSUPPORTED",
                },
            ],
            processingTrace=[],
            retryCount=0,
            skipReason=None,
        ),
    )

    matches = [
        (
            cast(
                "MailMessageProjectMatch",
                SimpleNamespace(
                    id=uuid4(),
                    projectId=project_a,
                    matchType=MailProjectMatchType.EXACT,
                    confidence=95,
                    matchedText="项目A",
                ),
            ),
            "项目A",
        ),
        (
            cast(
                "MailMessageProjectMatch",
                SimpleNamespace(
                    id=uuid4(),
                    projectId=project_b,
                    matchType=MailProjectMatchType.FUZZY,
                    confidence=82,
                    matchedText="项目B",
                ),
            ),
            "项目B",
        ),
    ]

    candidates = [
        (
            cast(
                "MailRiskCandidate",
                SimpleNamespace(
                    id=uuid4(),
                    projectId=project_a,
                    categoryId=category,
                    level=ProjectRiskLevel.HIGH,
                    description="风险A",
                    evidence="证据A",
                    suggestion="建议A",
                    confidence=90,
                    status=MailRiskCandidateStatus.PENDING,
                    confirmedRiskId=None,
                    reviewedAt=None,
                ),
            ),
            "项目A",
            "交付风险",
        ),
        (
            cast(
                "MailRiskCandidate",
                SimpleNamespace(
                    id=uuid4(),
                    projectId=project_b,
                    categoryId=category,
                    level=ProjectRiskLevel.MEDIUM,
                    description="风险B",
                    evidence="证据B",
                    suggestion="建议B",
                    confidence=80,
                    status=MailRiskCandidateStatus.PENDING,
                    confirmedRiskId=None,
                    reviewedAt=None,
                ),
            ),
            "项目B",
            "交付风险",
        ),
    ]

    detail = MailSyncResultsService._detail_item(
        message,
        "MAIL-TEST",
        matches,
        (2, 2),
        candidates,
    )

    assert [item.projectName for item in detail.projectMatches] == ["项目A", "项目B"]
    assert [item.projectName for item in detail.riskCandidates] == ["项目A", "项目B"]
    assert detail.riskCandidateCount == 2
    assert detail.pendingRiskCount == 2

    assert len(detail.attachments) == 2
    assert detail.attachments[0].type == "XLSX"
    assert detail.attachments[0].status == "PARSED"
    assert detail.attachments[0].summary is None
    assert detail.attachments[1].type == "EXE"
    assert detail.attachments[1].status == "SKIPPED"
    assert detail.attachments[1].summary == "UNSUPPORTED"


def test_detail_maps_parser_failure_attachment_to_failed_contract_status() -> None:
    message = cast(
        "MailMessage",
        SimpleNamespace(
            id=uuid4(),
            batchId=uuid4(),
            status=MailMessageStatus.COMPLETED,
            subject="项目周报",
            senderName=None,
            senderAddress=None,
            sentAt=None,
            processedAt=None,
            projectResolutionCandidates=None,
            projectResolutionStatus=None,
            failureSummary=None,
            keyPoints=[],
            sanitizedSummary=None,
            attachmentMetadata=[
                {
                    "name": "broken.pdf",
                    "mimeType": "application/pdf",
                    "extension": ".pdf",
                    "sizeBytes": 100,
                    "allowedFormat": "PDF",
                    "status": "TYPE_MISMATCH",
                    "code": "TYPE_MISMATCH",
                }
            ],
            processingTrace=[],
            retryCount=0,
            skipReason=None,
        ),
    )

    detail = MailSyncResultsService._detail_item(message, "MAIL-TEST", [], (0, 0), [])

    assert detail.attachments[0].type == "PDF"
    assert detail.attachments[0].status == "FAILED"
    assert detail.attachments[0].summary == "TYPE_MISMATCH"
