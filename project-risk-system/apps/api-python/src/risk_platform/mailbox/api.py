from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.auth.service import SessionIdentity
from risk_platform.mailbox.extraction import MailRiskCandidateService
from risk_platform.mailbox.resolution import MailProjectResolutionService
from risk_platform.mailbox.schemas import (
    MailboxConfigRequest,
    MailboxConnectionTestResult,
    MailboxOverview,
    MailboxStatusRequest,
    MailProjectResolutionConfirmRequest,
    MailRiskCandidateResponse,
    MailRiskCandidateUpdateRequest,
    MailSyncBatchResponse,
)
from risk_platform.mailbox.service import MailboxService
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/mailbox/me", tags=["mailbox"])
candidate_router = APIRouter(prefix="/mailbox", tags=["mailbox"])
Identity = Annotated[SessionIdentity, Depends(require_permissions("mailbox.manage_self"))]


def get_mailbox_service(request: Request) -> MailboxService:
    service = getattr(request.app.state, "mailbox_service", None)
    if not isinstance(service, MailboxService):
        raise RuntimeError("mailbox service is not configured")
    return service


def get_candidate_service(request: Request) -> MailRiskCandidateService:
    service = getattr(request.app.state, "mail_risk_candidate_service", None)
    if not isinstance(service, MailRiskCandidateService):
        raise RuntimeError("mail risk candidate service is not configured")
    return service


def get_resolution_service(request: Request) -> MailProjectResolutionService:
    service = getattr(request.app.state, "mail_project_resolution_service", None)
    if not isinstance(service, MailProjectResolutionService):
        raise RuntimeError("mail project resolution service is not configured")
    return service


CandidateIdentity = Annotated[
    SessionIdentity, Depends(require_permissions("mailbox.sync_self", "risk.resolve"))
]


@candidate_router.post("/messages/{message_id}/project-resolution")
async def confirm_project_resolution(
    request: Request,
    message_id: UUID,
    payload: MailProjectResolutionConfirmRequest,
    identity: CandidateIdentity,
    service: Annotated[MailProjectResolutionService, Depends(get_resolution_service)],
) -> ApiResponse[dict[str, str]]:
    await service.confirm(message_id, payload.projectId, identity)
    return ok(request, {"status": "RESUMED"}, "项目已确认: 邮件将继续进行风险识别")


@candidate_router.patch(
    "/risk-candidates/{candidate_id}", response_model=ApiResponse[MailRiskCandidateResponse]
)
async def update_candidate(
    request: Request,
    candidate_id: UUID,
    payload: MailRiskCandidateUpdateRequest,
    identity: CandidateIdentity,
    service: Annotated[MailRiskCandidateService, Depends(get_candidate_service)],
) -> ApiResponse[MailRiskCandidateResponse]:
    return ok(
        request, await service.update(candidate_id, payload, identity, UUID(get_trace_id(request)))
    )


@candidate_router.post(
    "/risk-candidates/{candidate_id}/ignore", response_model=ApiResponse[MailRiskCandidateResponse]
)
async def ignore_candidate(
    request: Request,
    candidate_id: UUID,
    identity: CandidateIdentity,
    service: Annotated[MailRiskCandidateService, Depends(get_candidate_service)],
) -> ApiResponse[MailRiskCandidateResponse]:
    return ok(request, await service.ignore(candidate_id, identity, UUID(get_trace_id(request))))


@candidate_router.post(
    "/risk-candidates/{candidate_id}/confirm", response_model=ApiResponse[MailRiskCandidateResponse]
)
async def confirm_candidate(
    request: Request,
    candidate_id: UUID,
    identity: CandidateIdentity,
    service: Annotated[MailRiskCandidateService, Depends(get_candidate_service)],
) -> ApiResponse[MailRiskCandidateResponse]:
    return ok(
        request, await service.confirm_response(candidate_id, identity, UUID(get_trace_id(request)))
    )


@router.get("", response_model=ApiResponse[MailboxOverview])
async def overview(
    request: Request,
    identity: Identity,
    service: Annotated[MailboxService, Depends(get_mailbox_service)],
) -> ApiResponse[MailboxOverview]:
    return ok(request, await service.overview(identity))


@router.put("", response_model=ApiResponse[MailboxOverview])
async def save(
    request: Request,
    payload: MailboxConfigRequest,
    identity: Identity,
    service: Annotated[MailboxService, Depends(get_mailbox_service)],
) -> ApiResponse[MailboxOverview]:
    return ok(
        request,
        await service.save(payload, identity, UUID(get_trace_id(request))),
        "个人邮箱配置已安全保存",
    )


@router.post("/test", response_model=ApiResponse[MailboxConnectionTestResult])
async def test(
    request: Request,
    payload: MailboxConfigRequest,
    identity: Identity,
    service: Annotated[MailboxService, Depends(get_mailbox_service)],
) -> ApiResponse[MailboxConnectionTestResult]:
    result = await service.test(payload, identity, UUID(get_trace_id(request)))
    return ok(request, result, "邮箱连接测试通过" if result.success else "邮箱连接测试失败")


@router.post("/status", response_model=ApiResponse[MailboxOverview])
async def status(
    request: Request,
    payload: MailboxStatusRequest,
    identity: Identity,
    service: Annotated[MailboxService, Depends(get_mailbox_service)],
) -> ApiResponse[MailboxOverview]:
    return ok(
        request,
        await service.set_status(payload.enabled, identity, UUID(get_trace_id(request))),
        "邮箱已恢复" if payload.enabled else "邮箱已停用",
    )


@router.post("/sync", response_model=ApiResponse[MailSyncBatchResponse])
async def sync(
    request: Request,
    identity: Annotated[
        SessionIdentity, Depends(require_permissions("mailbox.manage_self", "mailbox.sync_self"))
    ],
    service: Annotated[MailboxService, Depends(get_mailbox_service)],
) -> ApiResponse[MailSyncBatchResponse]:
    return ok(
        request,
        await service.enqueue_sync(identity, UUID(get_trace_id(request))),
        "邮箱同步任务已进入队列",
    )


__all__ = [
    "candidate_router",
    "get_candidate_service",
    "get_mailbox_service",
    "get_resolution_service",
    "router",
]
