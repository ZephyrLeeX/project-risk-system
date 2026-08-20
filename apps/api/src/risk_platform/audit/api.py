"""Compatible audit administration routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from risk_platform.audit.http import AuditQueryService
from risk_platform.audit.schemas import (
    AuditExportRequest,
    AuditListQuery,
    AuditLogDetail,
    AuditLogIntegrity,
    AuditLogOptions,
    AuditLogSummary,
    PaginatedAuditLogs,
)
from risk_platform.auth.api import validate_request_origin
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin/audit-logs", tags=["audit"])


def get_audit_query_service(request: Request) -> AuditQueryService:
    service = getattr(request.app.state, "audit_query_service", None)
    if not isinstance(service, AuditQueryService):
        raise RuntimeError("audit query service is not configured")
    return service


@router.get("/summary", response_model=ApiResponse[AuditLogSummary])
async def summary(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.audit.view"))],
    service: Annotated[AuditQueryService, Depends(get_audit_query_service)],
) -> ApiResponse[AuditLogSummary]:
    return ok(request, await service.summary(identity))


@router.get("/options", response_model=ApiResponse[AuditLogOptions])
async def options(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.audit.view"))],
    service: Annotated[AuditQueryService, Depends(get_audit_query_service)],
) -> ApiResponse[AuditLogOptions]:
    return ok(request, await service.options(identity))


@router.get("/integrity", response_model=ApiResponse[AuditLogIntegrity])
async def integrity(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.audit.view"))],
    service: Annotated[AuditQueryService, Depends(get_audit_query_service)],
) -> ApiResponse[AuditLogIntegrity]:
    return ok(request, await service.integrity(identity))


@router.get("", response_model=ApiResponse[PaginatedAuditLogs])
async def list_logs(
    request: Request,
    query: Annotated[AuditListQuery, Depends()],
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.audit.view"))],
    service: Annotated[AuditQueryService, Depends(get_audit_query_service)],
) -> ApiResponse[PaginatedAuditLogs]:
    return ok(request, await service.list(identity, query))


@router.post("/export", dependencies=[Depends(validate_request_origin)])
async def export_logs(
    request: Request,
    payload: AuditExportRequest,
    identity: Annotated[
        SessionIdentity, Depends(require_permissions("admin.audit.view", "admin.audit.export"))
    ],
    service: Annotated[AuditQueryService, Depends(get_audit_query_service)],
) -> Response:
    exported = await service.export(identity, payload, UUID(get_trace_id(request)))
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{exported.filename}",
            "X-Export-Count": str(exported.count),
        },
    )


@router.get("/{audit_id}", response_model=ApiResponse[AuditLogDetail])
async def detail(
    request: Request,
    audit_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.audit.view"))],
    service: Annotated[AuditQueryService, Depends(get_audit_query_service)],
) -> ApiResponse[AuditLogDetail]:
    return ok(request, await service.detail(identity, audit_id))


__all__ = ["get_audit_query_service", "router"]
