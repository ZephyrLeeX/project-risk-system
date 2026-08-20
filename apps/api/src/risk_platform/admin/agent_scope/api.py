"""`/api/admin/agent/scope-rules` — Agent layer-1 scope rule administration."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.admin.agent_scope.schemas import (
    CreateScopeRuleRequest,
    ScopeRuleResponse,
    ScopeRuleTestRequest,
    ScopeRuleTestResponse,
    UpdateScopeRuleRequest,
)
from risk_platform.admin.agent_scope.service import AdminAgentScopeRulesService
from risk_platform.auth.api import validate_request_origin
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/admin", tags=["admin-agent-scope"])


def get_admin_agent_scope_service(request: Request) -> AdminAgentScopeRulesService:
    service = getattr(request.app.state, "admin_agent_scope_rule_service", None)
    if not isinstance(service, AdminAgentScopeRulesService):
        raise RuntimeError("admin agent scope rule service is not configured")
    return service


@router.get("/agent/scope-rules", response_model=ApiResponse[list[ScopeRuleResponse]])
async def list_scope_rules(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.scope.manage"))],
    service: Annotated[
        AdminAgentScopeRulesService, Depends(get_admin_agent_scope_service)
    ],
) -> ApiResponse[list[ScopeRuleResponse]]:
    del identity
    return ok(request, await service.list_rules())


@router.post(
    "/agent/scope-rules",
    response_model=ApiResponse[ScopeRuleResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def create_scope_rule(
    request: Request,
    payload: CreateScopeRuleRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.scope.manage"))],
    service: Annotated[
        AdminAgentScopeRulesService, Depends(get_admin_agent_scope_service)
    ],
) -> ApiResponse[ScopeRuleResponse]:
    return ok(
        request,
        await service.create(payload, identity, UUID(get_trace_id(request))),
        "范围规则创建成功（默认停用，请先用测试接口验证）",
    )


@router.patch(
    "/agent/scope-rules/{rule_id}",
    response_model=ApiResponse[ScopeRuleResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def update_scope_rule(
    request: Request,
    rule_id: UUID,
    payload: UpdateScopeRuleRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.scope.manage"))],
    service: Annotated[
        AdminAgentScopeRulesService, Depends(get_admin_agent_scope_service)
    ],
) -> ApiResponse[ScopeRuleResponse]:
    return ok(
        request,
        await service.update(rule_id, payload, identity, UUID(get_trace_id(request))),
        "范围规则已保存并立即生效",
    )


@router.delete(
    "/agent/scope-rules/{rule_id}",
    response_model=ApiResponse[None],
    dependencies=[Depends(validate_request_origin)],
)
async def delete_scope_rule(
    request: Request,
    rule_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.scope.manage"))],
    service: Annotated[
        AdminAgentScopeRulesService, Depends(get_admin_agent_scope_service)
    ],
) -> ApiResponse[None]:
    await service.remove(rule_id, identity, UUID(get_trace_id(request)))
    return ok(request, None, "范围规则已删除")


@router.post(
    "/agent/scope-rules/test",
    response_model=ApiResponse[ScopeRuleTestResponse],
    dependencies=[Depends(validate_request_origin)],
)
async def test_scope_rule(
    request: Request,
    payload: ScopeRuleTestRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.scope.manage"))],
    service: Annotated[
        AdminAgentScopeRulesService, Depends(get_admin_agent_scope_service)
    ],
) -> ApiResponse[ScopeRuleTestResponse]:
    del identity
    return ok(request, await service.test(payload.message))


__all__ = ["get_admin_agent_scope_service", "router"]
