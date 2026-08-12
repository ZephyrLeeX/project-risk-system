"""Agent conversation and authorized tool-directory routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok

from .schemas import (
    AgentConversationEnvelope,
    AgentConversationHistory,
    AgentHelpResponse,
    AgentMessageEnvelope,
    AgentMessagePage,
    AgentMessageRequest,
)
from .service import AgentConversationService
from .tools import AgentToolRegistry

router = APIRouter(prefix="/agent", tags=["agent"])


def get_agent_service(request: Request) -> AgentConversationService:
    service = getattr(request.app.state, "agent_conversation_service", None)
    if not isinstance(service, AgentConversationService):
        raise RuntimeError("agent conversation service is not configured")
    return service


def get_agent_tools(request: Request) -> AgentToolRegistry:
    registry = getattr(request.app.state, "agent_tool_registry", None)
    if not isinstance(registry, AgentToolRegistry):
        raise RuntimeError("agent tool registry is not configured")
    return registry


@router.get("/help", response_model=ApiResponse[AgentHelpResponse])
async def help(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    registry: Annotated[AgentToolRegistry, Depends(get_agent_tools)],
) -> ApiResponse[AgentHelpResponse]:
    return ok(request, AgentHelpResponse(tools=registry.help(identity)))


@router.post(
    "/conversations",
    response_model=ApiResponse[AgentConversationEnvelope],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    request: Request,
    payload: AgentMessageRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentConversationService, Depends(get_agent_service)],
) -> ApiResponse[AgentConversationEnvelope]:
    return ok(request, await service.create(identity, payload.message))


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[AgentMessageEnvelope],
    status_code=status.HTTP_202_ACCEPTED,
)
async def continue_conversation(
    request: Request,
    conversation_id: UUID,
    payload: AgentMessageRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentConversationService, Depends(get_agent_service)],
) -> ApiResponse[AgentMessageEnvelope]:
    return ok(
        request,
        await service.continue_conversation(identity, conversation_id, payload.message),
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ApiResponse[AgentConversationHistory],
)
async def history(
    request: Request,
    conversation_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentConversationService, Depends(get_agent_service)],
) -> ApiResponse[AgentConversationHistory]:
    return ok(request, await service.history(identity, conversation_id))


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[AgentMessagePage],
)
async def messages(
    request: Request,
    conversation_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentConversationService, Depends(get_agent_service)],
    after_sequence: Annotated[int, Query(alias="afterSequence", ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ApiResponse[AgentMessagePage]:
    return ok(
        request,
        await service.message_page(
            identity, conversation_id, after_sequence=after_sequence, limit=limit
        ),
    )


__all__ = ["get_agent_service", "get_agent_tools", "router"]
