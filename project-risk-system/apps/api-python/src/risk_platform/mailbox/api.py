from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.auth.service import SessionIdentity
from risk_platform.mailbox.schemas import (
    MailboxConfigRequest,
    MailboxConnectionTestResult,
    MailboxOverview,
    MailboxStatusRequest,
    MailSyncBatchResponse,
)
from risk_platform.mailbox.service import MailboxService
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/mailbox/me", tags=["mailbox"])
Identity = Annotated[SessionIdentity, Depends(require_permissions("mailbox.manage_self"))]


def get_mailbox_service(request: Request) -> MailboxService:
    service = getattr(request.app.state, "mailbox_service", None)
    if not isinstance(service, MailboxService):
        raise RuntimeError("mailbox service is not configured")
    return service


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


__all__ = ["get_mailbox_service", "router"]
