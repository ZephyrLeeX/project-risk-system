"""Cookie-session administration API for retention holds."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from risk_platform.auth.api import current_identity, validate_request_origin
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.retention.schemas import (
    CreateRetentionHoldRequest,
    ReleaseRetentionHoldRequest,
    RetentionHoldListResponse,
    RetentionHoldQuery,
    RetentionHoldResponse,
)
from risk_platform.retention.service import RetentionHoldService
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin/retention-holds", tags=["retention"])


def get_retention_hold_service(request: Request) -> RetentionHoldService:
    service = getattr(request.app.state, "retention_hold_service", None)
    if not isinstance(service, RetentionHoldService):
        raise RuntimeError("retention hold service is not configured")
    return service


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[RetentionHoldResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def create_hold(
    request: Request,
    response: Response,
    payload: CreateRetentionHoldRequest,
    identity: Annotated[SessionIdentity, Depends(current_identity)],
    service: Annotated[RetentionHoldService, Depends(get_retention_hold_service)],
) -> ApiResponse[RetentionHoldResponse]:
    result = await service.create(
        resource_type=payload.resourceType,
        resource_id=payload.resourceId,
        reason=payload.reason,
        expires_at=payload.expiresAt,
        identity=identity,
        trace_id=UUID(get_trace_id(request)),
        as_of=datetime.now(UTC),
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
        return ok(request, RetentionHoldResponse.from_hold(result.hold))
    return ok(request, RetentionHoldResponse.from_hold(result.hold), "留存保全已创建")


@router.get("", response_model=ApiResponse[RetentionHoldListResponse])
async def list_holds(
    request: Request,
    query: Annotated[RetentionHoldQuery, Depends()],
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.config.manage"))],
    service: Annotated[RetentionHoldService, Depends(get_retention_hold_service)],
) -> ApiResponse[RetentionHoldListResponse]:
    del identity
    items, total = await service.list_holds(
        resource_type=query.resourceType,
        resource_id=query.resourceId,
        status=query.status,
        page=query.page,
        page_size=query.pageSize,
    )
    return ok(
        request,
        RetentionHoldListResponse(
            items=[RetentionHoldResponse.from_hold(row) for row in items],
            total=total,
            page=query.page,
            pageSize=query.pageSize,
        ),
    )


@router.get("/{hold_id}", response_model=ApiResponse[RetentionHoldResponse])
async def hold_detail(
    request: Request,
    hold_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.config.manage"))],
    service: Annotated[RetentionHoldService, Depends(get_retention_hold_service)],
) -> ApiResponse[RetentionHoldResponse]:
    del identity
    return ok(request, RetentionHoldResponse.from_hold(await service.get(hold_id)))


@router.post(
    "/{hold_id}/release",
    response_model=ApiResponse[RetentionHoldResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def release_hold(
    request: Request,
    hold_id: UUID,
    payload: ReleaseRetentionHoldRequest,
    identity: Annotated[SessionIdentity, Depends(current_identity)],
    service: Annotated[RetentionHoldService, Depends(get_retention_hold_service)],
) -> ApiResponse[RetentionHoldResponse]:
    del payload
    hold = await service.release(
        hold_id=hold_id,
        identity=identity,
        trace_id=UUID(get_trace_id(request)),
        as_of=datetime.now(UTC),
    )
    return ok(request, RetentionHoldResponse.from_hold(hold), "留存保全已解除")


__all__ = ["get_retention_hold_service", "router"]
