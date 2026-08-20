"""Additive `/api/admin/ai-provider-v2` administration routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.ai_providers.v2_schemas import (
    CreateModelConfigRequest,
    CreateProviderAccountRequest,
    DiscoveredModelResponse,
    ModelConfigResponse,
    ModelConfigStatusRequest,
    ProviderAccountResponse,
    ProviderAccountStatusRequest,
    ProviderV2ConnectionResult,
    RotateProviderAccountKeyRequest,
    UpdateModelConfigRequest,
    UpdateProviderAccountRequest,
)
from risk_platform.ai_providers.v2_service import AiProviderV2Service
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin/ai-provider-v2", tags=["ai-provider-v2"])


def get_ai_provider_v2_service(request: Request) -> AiProviderV2Service:
    service = getattr(request.app.state, "ai_provider_v2_service", None)
    if not isinstance(service, AiProviderV2Service):
        raise RuntimeError("AI Provider V2 service is not configured")
    return service


Identity = Annotated[SessionIdentity, Depends(require_permissions("admin.ai.manage"))]
Service = Annotated[AiProviderV2Service, Depends(get_ai_provider_v2_service)]


@router.get("/accounts", response_model=ApiResponse[list[ProviderAccountResponse]])
async def list_accounts(
    request: Request, identity: Identity, service: Service
) -> ApiResponse[list[ProviderAccountResponse]]:
    del identity
    return ok(request, await service.list_accounts())


@router.post("/accounts", response_model=ApiResponse[ProviderAccountResponse])
async def create_account(
    request: Request,
    payload: CreateProviderAccountRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderAccountResponse]:
    return ok(
        request,
        await service.create_account(payload, identity, UUID(get_trace_id(request))),
        "Provider Account 已创建",
    )


@router.patch("/accounts/{account_id}", response_model=ApiResponse[ProviderAccountResponse])
async def update_account(
    request: Request,
    account_id: UUID,
    payload: UpdateProviderAccountRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderAccountResponse]:
    return ok(
        request,
        await service.update_account(account_id, payload, identity, UUID(get_trace_id(request))),
        "Provider Account 已更新",
    )


@router.delete("/accounts/{account_id}", response_model=ApiResponse[None])
async def delete_account(
    request: Request, account_id: UUID, identity: Identity, service: Service
) -> ApiResponse[None]:
    await service.delete_account(account_id, identity, UUID(get_trace_id(request)))
    return ok(request, None, "Provider Account 已删除")


@router.post(
    "/accounts/{account_id}/rotate-key", response_model=ApiResponse[ProviderAccountResponse]
)
async def rotate_key(
    request: Request,
    account_id: UUID,
    payload: RotateProviderAccountKeyRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderAccountResponse]:
    return ok(
        request,
        await service.rotate_key(account_id, payload, identity, UUID(get_trace_id(request))),
        "Provider Account API Key 已轮换",
    )


@router.post(
    "/accounts/{account_id}/status", response_model=ApiResponse[ProviderAccountResponse]
)
async def set_account_status(
    request: Request,
    account_id: UUID,
    payload: ProviderAccountStatusRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderAccountResponse]:
    return ok(
        request,
        await service.set_account_status(
            account_id, payload, identity, UUID(get_trace_id(request))
        ),
        "Provider Account 状态已更新",
    )


@router.get(
    "/accounts/{account_id}/models/discover",
    response_model=ApiResponse[list[DiscoveredModelResponse]],
)
async def discover_models(
    request: Request, account_id: UUID, identity: Identity, service: Service
) -> ApiResponse[list[DiscoveredModelResponse]]:
    return ok(
        request,
        await service.discover_models(account_id, identity, UUID(get_trace_id(request))),
    )


@router.post(
    "/accounts/{account_id}/test", response_model=ApiResponse[ProviderV2ConnectionResult]
)
async def test_account(
    request: Request, account_id: UUID, identity: Identity, service: Service
) -> ApiResponse[ProviderV2ConnectionResult]:
    return ok(
        request,
        await service.test_account(account_id, identity, UUID(get_trace_id(request))),
        "Provider Account 测试已完成",
    )


@router.get(
    "/accounts/{account_id}/models", response_model=ApiResponse[list[ModelConfigResponse]]
)
async def list_models(
    request: Request, account_id: UUID, identity: Identity, service: Service
) -> ApiResponse[list[ModelConfigResponse]]:
    del identity
    return ok(request, await service.list_models(account_id))


@router.post(
    "/accounts/{account_id}/models", response_model=ApiResponse[ModelConfigResponse]
)
async def create_model(
    request: Request,
    account_id: UUID,
    payload: CreateModelConfigRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ModelConfigResponse]:
    return ok(
        request,
        await service.create_model(
            account_id, payload, identity, UUID(get_trace_id(request))
        ),
        "Model Config 已创建",
    )


@router.patch(
    "/accounts/{account_id}/models/{model_id}",
    response_model=ApiResponse[ModelConfigResponse],
)
async def update_model(
    request: Request,
    account_id: UUID,
    model_id: UUID,
    payload: UpdateModelConfigRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ModelConfigResponse]:
    return ok(
        request,
        await service.update_model(
            account_id, model_id, payload, identity, UUID(get_trace_id(request))
        ),
        "Model Config 已更新",
    )


@router.delete("/accounts/{account_id}/models/{model_id}", response_model=ApiResponse[None])
async def delete_model(
    request: Request,
    account_id: UUID,
    model_id: UUID,
    identity: Identity,
    service: Service,
) -> ApiResponse[None]:
    await service.delete_model(account_id, model_id, identity, UUID(get_trace_id(request)))
    return ok(request, None, "Model Config 已删除")


@router.post(
    "/accounts/{account_id}/models/{model_id}/status",
    response_model=ApiResponse[ModelConfigResponse],
)
async def set_model_status(
    request: Request,
    account_id: UUID,
    model_id: UUID,
    payload: ModelConfigStatusRequest,
    identity: Identity,
    service: Service,
) -> ApiResponse[ModelConfigResponse]:
    return ok(
        request,
        await service.set_model_status(
            account_id, model_id, payload, identity, UUID(get_trace_id(request))
        ),
        "Model Config 状态已更新",
    )


@router.post(
    "/accounts/{account_id}/models/{model_id}/set-default",
    response_model=ApiResponse[ModelConfigResponse],
)
async def set_default_model(
    request: Request,
    account_id: UUID,
    model_id: UUID,
    identity: Identity,
    service: Service,
) -> ApiResponse[ModelConfigResponse]:
    return ok(
        request,
        await service.set_default_model(
            account_id, model_id, identity, UUID(get_trace_id(request))
        ),
        "默认 Model Config 已更新",
    )


@router.post(
    "/accounts/{account_id}/models/{model_id}/test",
    response_model=ApiResponse[ProviderV2ConnectionResult],
)
async def test_model(
    request: Request,
    account_id: UUID,
    model_id: UUID,
    identity: Identity,
    service: Service,
) -> ApiResponse[ProviderV2ConnectionResult]:
    return ok(
        request,
        await service.test_model(
            account_id, model_id, identity, UUID(get_trace_id(request))
        ),
        "Model Config 测试已完成",
    )


__all__ = ["get_ai_provider_v2_service", "router"]
