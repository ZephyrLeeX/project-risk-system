"""Agent conversation and authorized tool-directory routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse

from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

from .confirmation import AgentConfirmationService
from .interaction import AgentInteractionService
from .schemas import (
    AgentConfirmationRequest,
    AgentConfirmationResponse,
    AgentConversationEnvelope,
    AgentConversationHistory,
    AgentConversationRuntime,
    AgentHelpResponse,
    AgentInteractionRespondRequest,
    AgentInteractionRespondResponse,
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


def get_confirmation_service(request: Request) -> AgentConfirmationService:
    return AgentConfirmationService(get_agent_service(request).session_factory)


def get_interaction_service(request: Request) -> AgentInteractionService:
    return AgentInteractionService(get_agent_service(request).session_factory)


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


@router.get(
    "/conversations/{conversation_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(
    request: Request,
    conversation_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentConversationService, Depends(get_agent_service)],
    after: Annotated[UUID | None, Query()] = None,
    after_sequence: Annotated[int | None, Query(alias="afterSequence", ge=0)] = None,
) -> StreamingResponse:
    event_stream = await service.events(identity, conversation_id, after, after_sequence)
    return StreamingResponse(
        event_stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/conversations/{conversation_id}/cancel",
    response_model=ApiResponse[AgentConversationRuntime],
)
async def cancel(
    request: Request,
    conversation_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentConversationService, Depends(get_agent_service)],
) -> ApiResponse[AgentConversationRuntime]:
    return ok(request, await service.cancel(identity, conversation_id))


@router.post(
    "/interactions/{interactionId}/respond",
    response_model=ApiResponse[AgentInteractionRespondResponse],
)
async def respond_interaction(
    request: Request,
    interactionId: UUID,
    payload: AgentInteractionRespondRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentInteractionService, Depends(get_interaction_service)],
) -> ApiResponse[AgentInteractionRespondResponse]:
    return ok(
        request, await service.respond(identity, interactionId, payload, get_trace_id(request))
    )


@router.post(
    "/confirmations/{token}",
    response_model=ApiResponse[AgentConfirmationResponse],
    deprecated=True,
    summary="Legacy Agent confirmation compatibility endpoint",
    description=(
        "Deprecated compatibility surface retained under the approved legacy API contract. "
        "It is not consumed by Agent V2 Core, Interaction, Mutation, worker, or the active "
        "Vue path. "
        "New writes use AgentInteraction.respond."
    ),
)
async def confirm(
    request: Request,
    token: str,
    payload: AgentConfirmationRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("agent.use"))],
    service: Annotated[AgentConfirmationService, Depends(get_confirmation_service)],
) -> ApiResponse[AgentConfirmationResponse]:
    del payload
    return ok(
        request,
        AgentConfirmationResponse.model_validate(
            await service.confirm(identity, token, UUID(get_trace_id(request)))
        ),
    )


__all__ = ["get_agent_service", "get_agent_tools", "router"]
