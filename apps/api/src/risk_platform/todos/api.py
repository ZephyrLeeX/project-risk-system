"""Compatible manager todo routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from risk_platform.auth.api import validate_request_origin
from risk_platform.auth.service import SessionIdentity
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id
from risk_platform.todos.schemas import (
    ListTodosQuery,
    ManagerTodoDetail,
    ManagerTodoListResponse,
    UpdateTodoRequest,
)
from risk_platform.todos.service import TodosService

router = APIRouter(prefix="/todos", tags=["todos"])


def get_todos_service(request: Request) -> TodosService:
    service = getattr(request.app.state, "todos_service", None)
    if not isinstance(service, TodosService):
        raise RuntimeError("todo service is not configured")
    return service


@router.get("", response_model=ApiResponse[ManagerTodoListResponse])
async def list_todos(
    request: Request,
    query: Annotated[ListTodosQuery, Depends()],
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[TodosService, Depends(get_todos_service)],
) -> ApiResponse[ManagerTodoListResponse]:
    return ok(request, await service.list(identity, query))


@router.get("/{todo_id}", response_model=ApiResponse[ManagerTodoDetail])
async def detail(
    request: Request,
    todo_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("dashboard.view"))],
    service: Annotated[TodosService, Depends(get_todos_service)],
) -> ApiResponse[ManagerTodoDetail]:
    return ok(request, await service.detail(identity, todo_id))


@router.patch(
    "/{todo_id}",
    response_model=ApiResponse[ManagerTodoDetail],
    dependencies=[Depends(validate_request_origin)],
)
async def update(
    request: Request,
    todo_id: UUID,
    payload: UpdateTodoRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("risk.resolve"))],
    service: Annotated[TodosService, Depends(get_todos_service)],
) -> ApiResponse[ManagerTodoDetail]:
    return ok(
        request,
        await service.update(identity, todo_id, payload, UUID(get_trace_id(request))),
        "待办事项已更新",
    )


__all__ = ["get_todos_service", "router"]
