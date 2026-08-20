"""Compatible `/api/admin/ai-services` routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.ai_providers.schemas import (
    CallDetail,
    CallsQuery,
    ConnectionResult,
    CreateProviderRequest,
    DraftTestRequest,
    PageResponse,
    ProviderQuery,
    ProviderResponse,
    ProviderStatusRequest,
    ProviderStrategy,
    ProviderSummary,
    RotateKeyRequest,
    UpdateProviderRequest,
    UsageOverview,
    UsageQuery,
)
from risk_platform.ai_providers.service import AiProvidersService
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin/ai-services", tags=["ai-providers"])


def get_ai_providers_service(request: Request) -> AiProvidersService:
    service = getattr(request.app.state, "ai_providers_service", None)
    if not isinstance(service, AiProvidersService):
        raise RuntimeError("AI provider service is not configured")
    return service


Identity = Annotated[SessionIdentity, Depends(require_permissions("admin.ai.manage"))]
Service = Annotated[AiProvidersService, Depends(get_ai_providers_service)]


@router.get("/summary", response_model=ApiResponse[ProviderSummary])
async def summary(
    request: Request, identity: Identity, service: Service
) -> ApiResponse[ProviderSummary]:
    del identity
    return ok(request, await service.summary())


@router.get("/strategy", response_model=ApiResponse[list[ProviderStrategy]])
async def strategy(
    request: Request, identity: Identity, service: Service
) -> ApiResponse[list[ProviderStrategy]]:
    del identity
    return ok(request, await service.strategy())


@router.get("/usage", response_model=ApiResponse[UsageOverview])
async def usage(
    request: Request, query: Annotated[UsageQuery, Depends()], identity: Identity, service: Service
) -> ApiResponse[UsageOverview]:
    del identity
    return ok(request, await service.usage(query))


@router.get("/calls", response_model=ApiResponse[PageResponse])
async def calls(
    request: Request, query: Annotated[CallsQuery, Depends()], identity: Identity, service: Service
) -> ApiResponse[PageResponse]:
    del identity
    return ok(request, await service.calls(query))


@router.get("/calls/{call_id}", response_model=ApiResponse[CallDetail])
async def call_detail(
    request: Request, call_id: UUID, identity: Identity, service: Service
) -> ApiResponse[CallDetail]:
    del identity
    return ok(request, await service.call_detail(call_id))


@router.post("/test-draft", response_model=ApiResponse[ConnectionResult])
async def test_draft(
    request: Request, payload: DraftTestRequest, identity: Identity, service: Service
) -> ApiResponse[ConnectionResult]:
    return ok(
        request,
        await service.test_draft(payload, identity, UUID(get_trace_id(request))),
        "连接测试已完成",
    )


@router.post("/test-all", response_model=ApiResponse[list[ConnectionResult]])
async def test_all(
    request: Request, identity: Identity, service: Service
) -> ApiResponse[list[ConnectionResult]]:
    return ok(
        request, await service.test_all(identity, UUID(get_trace_id(request))), "批量连接测试已完成"
    )


@router.get("", response_model=ApiResponse[list[ProviderResponse]])
async def list_providers(
    request: Request,
    query: Annotated[ProviderQuery, Depends()],
    identity: Identity,
    service: Service,
) -> ApiResponse[list[ProviderResponse]]:
    del identity
    return ok(request, await service.list(query))


@router.post("", response_model=ApiResponse[ProviderResponse])
async def create(
    request: Request, payload: CreateProviderRequest, identity: Identity, service: Service
) -> ApiResponse[ProviderResponse]:
    return ok(
        request,
        await service.create(payload, identity, UUID(get_trace_id(request))),
        "AI服务配置已创建",
    )


@router.patch("/{provider_id}", response_model=ApiResponse[ProviderResponse])
async def update(
    request: Request,
    provider_id: UUID,
    payload: UpdateProviderRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderResponse]:
    return ok(
        request,
        await service.update(provider_id, payload, identity, UUID(get_trace_id(request))),
        "AI服务配置已保存",
    )


@router.post("/{provider_id}/rotate-key", response_model=ApiResponse[ProviderResponse])
async def rotate_key(
    request: Request,
    provider_id: UUID,
    payload: RotateKeyRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderResponse]:
    return ok(
        request,
        await service.rotate_key(provider_id, payload, identity, UUID(get_trace_id(request))),
        "API Key已安全轮换",
    )


@router.post("/{provider_id}/test", response_model=ApiResponse[ConnectionResult])
async def test(
    request: Request, provider_id: UUID, identity: Identity, service: Service
) -> ApiResponse[ConnectionResult]:
    return ok(
        request,
        await service.test_provider(provider_id, identity, UUID(get_trace_id(request))),
        "连接测试已完成",
    )


@router.post("/{provider_id}/set-default", response_model=ApiResponse[ProviderResponse])
async def set_default(
    request: Request, provider_id: UUID, identity: Identity, service: Service
) -> ApiResponse[ProviderResponse]:
    return ok(
        request,
        await service.set_default(provider_id, identity, UUID(get_trace_id(request))),
        "默认AI服务已切换",
    )


@router.post("/{provider_id}/status", response_model=ApiResponse[ProviderResponse])
async def set_status(
    request: Request,
    provider_id: UUID,
    payload: ProviderStatusRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderResponse]:
    return ok(
        request,
        await service.set_status(provider_id, payload, identity, UUID(get_trace_id(request))),
        "AI服务状态已更新",
    )


__all__ = ["get_ai_providers_service", "router"]
