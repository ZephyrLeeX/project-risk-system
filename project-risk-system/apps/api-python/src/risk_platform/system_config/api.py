"""Compatible system configuration administration routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.system_config.schemas import ConfigOverview, ProjectOptionResponse, PublishRequest, ReleaseDetail, ReleaseItem, ReleaseQuery
from risk_platform.system_config.service import SystemConfigService
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin/system-config", tags=["system-config"])


def get_system_config_service(request: Request) -> SystemConfigService:
    service = getattr(request.app.state, "system_config_service", None)
    if not isinstance(service, SystemConfigService):
        raise RuntimeError("system config service is not configured")
    return service


@router.get("", response_model=ApiResponse[ConfigOverview])
async def overview(request: Request, identity: Annotated[SessionIdentity, Depends(require_permissions("admin.config.manage"))], service: Annotated[SystemConfigService, Depends(get_system_config_service)]) -> ApiResponse[ConfigOverview]:
    del identity
    return ok(request, await service.overview())


@router.get("/project-options", response_model=ApiResponse[list[ProjectOptionResponse]])
async def project_options(request: Request, identity: Annotated[SessionIdentity, Depends(require_permissions("admin.config.manage"))], service: Annotated[SystemConfigService, Depends(get_system_config_service)]) -> ApiResponse[list[ProjectOptionResponse]]:
    del identity
    return ok(request, await service.project_options())


@router.post("/publish", response_model=ApiResponse[ConfigOverview])
async def publish(request: Request, payload: PublishRequest, identity: Annotated[SessionIdentity, Depends(require_permissions("admin.config.manage"))], service: Annotated[SystemConfigService, Depends(get_system_config_service)]) -> ApiResponse[ConfigOverview]:
    return ok(request, await service.publish(payload, identity, UUID(get_trace_id(request))), "系统配置已保存并发布")


@router.get("/releases", response_model=ApiResponse[list[ReleaseItem]])
async def releases(request: Request, query: Annotated[ReleaseQuery, Depends()], identity: Annotated[SessionIdentity, Depends(require_permissions("admin.config.manage"))], service: Annotated[SystemConfigService, Depends(get_system_config_service)]) -> ApiResponse[list[ReleaseItem]]:
    del identity
    return ok(request, await service.releases(query))


@router.get("/releases/{release_id}", response_model=ApiResponse[ReleaseDetail])
async def release_detail(request: Request, release_id: UUID, identity: Annotated[SessionIdentity, Depends(require_permissions("admin.config.manage"))], service: Annotated[SystemConfigService, Depends(get_system_config_service)]) -> ApiResponse[ReleaseDetail]:
    del identity
    return ok(request, await service.release_detail(release_id))


__all__ = ["get_system_config_service", "router"]
