"""T043 mailbox sync-results browse/retry HTTP surface.

Exposes the legacy ``/mailbox`` sync-results endpoints under
``mailbox.sync_self`` permission gating; ``MailSyncResultsService`` enforces the
``RISK_ADMIN`` role and own-config scope. The router is registered additively by
the composition root (``main.py``) and owns no production dependencies.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from risk_platform.auth.service import SessionIdentity
from risk_platform.mailbox.sync_results.schemas import (
    MailMessageDetail,
    MailMessageListQuery,
    MailMessageListResponse,
    MailMessageStatus,
    MailRiskReviewOptions,
    MailSyncBatchDetail,
    MailSyncBatchItem,
    MailSyncBatchListQuery,
    MailSyncBatchListResponse,
    MailSyncSummary,
)
from risk_platform.mailbox.sync_results.service import MailSyncResultsService
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/mailbox", tags=["mailbox"])

Identity = Annotated[SessionIdentity, Depends(require_permissions("mailbox.sync_self"))]


def get_sync_results_service(request: Request) -> MailSyncResultsService:
    service = getattr(request.app.state, "mail_sync_results_service", None)
    if not isinstance(service, MailSyncResultsService):
        raise RuntimeError("mail sync results service is not configured")
    return service


Service = Annotated[MailSyncResultsService, Depends(get_sync_results_service)]


@router.get("/sync-summary", response_model=ApiResponse[MailSyncSummary])
async def sync_summary(
    request: Request,
    identity: Identity,
    service: Service,
) -> ApiResponse[MailSyncSummary]:
    return ok(request, await service.summary(identity))


@router.get("/review-options", response_model=ApiResponse[MailRiskReviewOptions])
async def review_options(
    request: Request,
    identity: Identity,
    service: Service,
) -> ApiResponse[MailRiskReviewOptions]:
    return ok(request, await service.review_options(identity))


@router.get("/messages", response_model=ApiResponse[MailMessageListResponse])
async def messages(
    request: Request,
    identity: Identity,
    service: Service,
    keyword: Annotated[str | None, Query()] = None,
    status: Annotated[MailMessageStatus | None, Query()] = None,
    batchId: Annotated[UUID | None, Query()] = None,
    withRisk: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 10,
) -> ApiResponse[MailMessageListResponse]:
    query = MailMessageListQuery(
        keyword=keyword,
        status=status,
        batchId=batchId,
        withRisk=withRisk,
        page=page,
        pageSize=pageSize,
    )
    return ok(request, await service.messages(identity, query))


@router.get("/messages/{message_id}", response_model=ApiResponse[MailMessageDetail])
async def message(
    request: Request,
    message_id: UUID,
    identity: Identity,
    service: Service,
) -> ApiResponse[MailMessageDetail]:
    return ok(request, await service.message(identity, message_id))


@router.post("/messages/{message_id}/retry", response_model=ApiResponse[MailSyncBatchItem])
async def retry_message(
    request: Request,
    message_id: UUID,
    identity: Identity,
    service: Service,
) -> ApiResponse[MailSyncBatchItem]:
    return ok(
        request,
        await service.retry(message_id, identity, UUID(get_trace_id(request))),
        "失败邮件已进入重新处理队列",
    )


@router.get("/sync-batches", response_model=ApiResponse[MailSyncBatchListResponse])
async def sync_batches(
    request: Request,
    identity: Identity,
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 10,
) -> ApiResponse[MailSyncBatchListResponse]:
    query = MailSyncBatchListQuery(page=page, pageSize=pageSize)
    return ok(request, await service.batches(identity, query))


@router.get("/sync-batches/{batch_id}", response_model=ApiResponse[MailSyncBatchDetail])
async def sync_batch(
    request: Request,
    batch_id: UUID,
    identity: Identity,
    service: Service,
) -> ApiResponse[MailSyncBatchDetail]:
    return ok(request, await service.batch(identity, batch_id))


__all__ = ["get_sync_results_service", "router"]
