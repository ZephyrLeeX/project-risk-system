"""Public Agent conversation and read-only tool contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from risk_platform.model_types import JSONValue
from risk_platform.risks.schemas import RiskItem
from risk_platform.shared.http import StrictRequestModel


class _Contract(BaseModel):
    """ADR 0019/0028/0029 public-contract base.

    The wildcard ``field_serializer`` must NOT declare a return type: with a
    return annotation (e.g. ``-> object``) Pydantic replaces every field's
    serialization-mode JSON schema with that annotation, collapsing each
    ``_Contract`` field to ``unknown`` in the frozen OpenAPI authority. Omitting
    the return annotation lets Pydantic keep each field's declared type while
    the body still reformats ``datetime`` values to UTC RFC 3339 milliseconds
    with ``Z`` (``when_used="json"`` keeps Python ``model_dump`` returning
    datetime objects).
    """

    model_config = ConfigDict(extra="forbid")

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_values(self, value: object):  # type: ignore[no-untyped-def]
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return value


class AgentMessageResponse(_Contract):
    id: UUID
    sequence: int
    role: str
    content: str
    traceId: str
    dataAsOf: datetime | None
    createdAt: datetime


class AgentConversationResponse(_Contract):
    id: UUID
    createdAt: datetime
    updatedAt: datetime
    expiresAt: datetime
    lastMessageSequence: int
    lastEventSequence: int


class AgentConversationEnvelope(_Contract):
    conversation: AgentConversationResponse
    userMessage: AgentMessageResponse
    streamUrl: str


class AgentMessageEnvelope(_Contract):
    userMessage: AgentMessageResponse
    streamUrl: str


class AgentMessageRequest(StrictRequestModel):
    message: str = Field(min_length=1, max_length=4000)


class AgentConversationHistory(_Contract):
    conversation: AgentConversationResponse
    messages: list[AgentMessageResponse]
    nextMessageSequence: int


class AgentMessagePage(_Contract):
    items: list[AgentMessageResponse]
    nextAfterSequence: int


class AgentToolHelp(_Contract):
    name: str
    description: str
    requiredPermissions: list[str]
    supportsPreview: bool


class AgentHelpResponse(_Contract):
    tools: list[AgentToolHelp]


class AgentConfirmationResponse(_Contract):
    operation: str
    resourceType: str
    resourceId: UUID
    completedAt: datetime


class AgentConfirmationRequest(StrictRequestModel):
    pass


class AgentToolResult(_Contract):
    toolInvocationId: str
    tool: str
    data: JSONValue
    dataAsOf: datetime
    traceId: str
    provenance: str


class EmptyToolArguments(StrictRequestModel):
    pass


class ProjectSearchToolArguments(StrictRequestModel):
    keyword: str | None = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)
    status: str | None = Field(default=None, max_length=32)


# Compatibility import only; it is intentionally not exported in the V2 tool catalogue.
ProjectListToolArguments = ProjectSearchToolArguments


class ProjectListToolItem(_Contract):
    id: UUID
    name: str
    status: str


class ProjectListToolResponse(_Contract):
    items: list[ProjectListToolItem]
    page: int
    pageSize: int
    total: int


class ProjectDetailToolArguments(StrictRequestModel):
    projectId: UUID


class ProjectDetailToolResponse(_Contract):
    id: UUID
    name: str
    alias: str | None
    status: str


class RiskCategoryListToolResponse(_Contract):
    items: list[dict[str, str]]


class DashboardFocusToolResponse(_Contract):
    """Agent-specific envelope for the dashboard service's list return value."""

    items: list[RiskItem]


class RiskToolArguments(StrictRequestModel):
    keyword: str | None = Field(default=None, max_length=100)
    projectId: UUID | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class RiskDetailToolArguments(StrictRequestModel):
    riskId: UUID


class TodoToolArguments(StrictRequestModel):
    owner: str | None = Field(default=None, max_length=128)
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class TodoDetailToolArguments(StrictRequestModel):
    todoId: UUID


class WeeklyReportToolArguments(StrictRequestModel):
    weekStart: datetime | None = None


class WeeklyDetailToolArguments(StrictRequestModel):
    weekStart: datetime
    projectId: UUID


__all__ = [
    "AgentConfirmationRequest",
    "AgentConfirmationResponse",
    "AgentConversationEnvelope",
    "AgentConversationHistory",
    "AgentConversationResponse",
    "AgentHelpResponse",
    "AgentMessageEnvelope",
    "AgentMessagePage",
    "AgentMessageRequest",
    "AgentMessageResponse",
    "AgentToolHelp",
    "AgentToolResult",
    "DashboardFocusToolResponse",
    "EmptyToolArguments",
    "ProjectDetailToolArguments",
    "ProjectDetailToolResponse",
    "ProjectListToolItem",
    "ProjectListToolResponse",
    "ProjectSearchToolArguments",
    "RiskCategoryListToolResponse",
    "RiskDetailToolArguments",
    "RiskToolArguments",
    "TodoDetailToolArguments",
    "TodoToolArguments",
    "WeeklyDetailToolArguments",
    "WeeklyReportToolArguments",
]
