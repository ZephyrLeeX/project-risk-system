"""Compatible risk and dashboard timeline routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.auth.api import validate_request_origin
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.risks.schemas import LifecycleRequest, RiskDetail, RiskPage, RiskQuery, ResolvedRiskPage, TimelineDetail, TimelinePage, TimelineQuery
from risk_platform.risks.service import RisksService
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(tags=["risks"])


def get_risks_service(request: Request) -> RisksService:
    service = getattr(request.app.state, "risks_service", None)
    if not isinstance(service, RisksService):
        raise RuntimeError("risk service is not configured")
    return service


@router.get("/risks", response_model=ApiResponse[RiskPage])
async def list_risks(request: Request, query: Annotated[RiskQuery, Depends()], identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))], service: Annotated[RisksService, Depends(get_risks_service)]) -> ApiResponse[RiskPage]:
    result = await service.list(identity, query)
    assert isinstance(result, RiskPage)
    return ok(request, result)


@router.get("/risks/resolved", response_model=ApiResponse[ResolvedRiskPage])
async def resolved_risks(request: Request, query: Annotated[RiskQuery, Depends()], identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))], service: Annotated[RisksService, Depends(get_risks_service)]) -> ApiResponse[ResolvedRiskPage]:
    result = await service.list(identity, query, resolved=True)
    assert isinstance(result, ResolvedRiskPage)
    return ok(request, result)


@router.get("/risks/{risk_id}", response_model=ApiResponse[RiskDetail])
async def risk_detail(request: Request, risk_id: UUID, identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))], service: Annotated[RisksService, Depends(get_risks_service)]) -> ApiResponse[RiskDetail]:
    return ok(request, await service.detail(identity, risk_id))


@router.post("/risks/{risk_id}/resolve", response_model=ApiResponse[RiskDetail], dependencies=[Depends(validate_request_origin)])
async def resolve_risk(request: Request, risk_id: UUID, payload: LifecycleRequest, identity: Annotated[SessionIdentity, Depends(require_permissions("risk.resolve"))], service: Annotated[RisksService, Depends(get_risks_service)]) -> ApiResponse[RiskDetail]:
    result = await service.resolve(identity, risk_id, payload, UUID(get_trace_id(request)))
    return ok(request, result, "风险已解除")


@router.post("/risks/{risk_id}/reopen", response_model=ApiResponse[RiskDetail], dependencies=[Depends(validate_request_origin)])
async def reopen_risk(request: Request, risk_id: UUID, payload: LifecycleRequest, identity: Annotated[SessionIdentity, Depends(require_permissions("risk.resolve"))], service: Annotated[RisksService, Depends(get_risks_service)]) -> ApiResponse[RiskDetail]:
    result = await service.reopen(identity, risk_id, payload, UUID(get_trace_id(request)))
    return ok(request, result, "风险已重新打开")


@router.get("/dashboard/timeline", response_model=ApiResponse[TimelinePage])
async def timeline(request: Request, query: Annotated[TimelineQuery, Depends()], identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))], service: Annotated[RisksService, Depends(get_risks_service)]) -> ApiResponse[TimelinePage]:
    return ok(request, await service.timeline(identity, query))


@router.get("/dashboard/timeline/{event_id}", response_model=ApiResponse[TimelineDetail])
async def timeline_detail(request: Request, event_id: UUID, identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))], service: Annotated[RisksService, Depends(get_risks_service)]) -> ApiResponse[TimelineDetail]:
    return ok(request, await service.timeline_detail(identity, event_id))


__all__ = ["get_risks_service", "router"]
