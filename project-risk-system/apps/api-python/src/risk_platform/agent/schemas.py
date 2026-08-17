"""Public Agent conversation and read-only tool contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

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
    structured: dict[str, JSONValue] | None = None
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


class AgentInteractionResponse(_Contract):
    id: UUID
    type: str
    status: str
    conversationId: UUID
    executionId: UUID
    candidates: list[dict[str, JSONValue]]
    expiresAt: datetime


class AgentInteractionRespondRequest(StrictRequestModel):
    action: str = Field(pattern=r"^(SELECT|MANUAL_INPUT|CANCEL)$")
    projectId: UUID | None = None
    projectName: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def strict_action_payload(self) -> AgentInteractionRespondRequest:
        if self.action == "SELECT" and (self.projectId is None or self.projectName is not None):
            raise ValueError("SELECT requires only projectId")
        if self.action == "MANUAL_INPUT" and (
            self.projectName is None or self.projectId is not None
        ):
            raise ValueError("MANUAL_INPUT requires only projectName")
        if self.action == "CANCEL" and (self.projectId is not None or self.projectName is not None):
            raise ValueError("CANCEL does not accept a project")
        return self


class AgentInteractionRespondResponse(_Contract):
    interaction: AgentInteractionResponse
    streamUrl: str | None


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


class CandidateRiskBasisType(StrEnum):
    SYSTEM_FACT = "SYSTEM_FACT"
    AI_ANALYSIS = "AI_ANALYSIS"
    MIXED = "MIXED"


class CandidateRisk(_Contract):
    """Structured, non-business risk analysis produced by an Agent execution.

    This is deliberately not a Risk, MutationDraft, or Interaction.  The
    provenance rules are validated at the contract boundary; the execution
    service additionally verifies invocation ownership and authorization.
    """

    id: UUID
    projectId: UUID
    projectName: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    basisType: CandidateRiskBasisType
    evidenceSummary: str = Field(min_length=1)
    sourceInvocationIds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_basis(self) -> CandidateRisk:
        if self.basisType is CandidateRiskBasisType.AI_ANALYSIS:
            if self.sourceInvocationIds:
                raise ValueError("AI_ANALYSIS CandidateRisk cannot cite tool invocations")
            if "AI风险分析" not in self.evidenceSummary:
                raise ValueError("AI_ANALYSIS evidence must be explicitly labeled")
        elif not self.sourceInvocationIds:
            raise ValueError("system-grounded CandidateRisk requires tool provenance")
        if self.basisType is CandidateRiskBasisType.MIXED and not {
            "系统事实",
            "AI分析",
        }.issubset(self.evidenceSummary):
            raise ValueError("MIXED evidence must distinguish system facts from AI analysis")
        return self


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
    "CandidateRisk",
    "CandidateRiskBasisType",
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
