"""Public Agent conversation and read-only tool contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from risk_platform.model_types import JSONValue
from risk_platform.risks.models import ProjectRiskLevel, RiskStatus
from risk_platform.shared.http import StrictRequestModel
from risk_platform.shared.time_ranges import RiskTimeRangePreset


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


class AgentConversationListItem(_Contract):
    """Lightweight row for the "my history" list.

    ``title`` is derived from the conversation's first USER message (trimmed to
    ~40 characters, falling back to "新会话" when the conversation has no user
    text). It is NOT a stored column — no migration is needed. ``updatedAt``
    drives the DESC ordering the list is served in.
    """

    id: UUID
    title: str
    createdAt: datetime
    updatedAt: datetime
    activeProjectName: str | None
    lastMessageSequence: int


class AgentConversationListPage(_Contract):
    items: list[AgentConversationListItem]
    page: int
    pageSize: int
    total: int


class AgentConversationEnvelope(_Contract):
    """First-turn create response; also the source for the continue envelope.

    ``resumeAfterEventSequence`` is ``conversation.lastEventSequence`` snapshotted
    in the transaction that enqueues the durable task, BEFORE that task is
    visible to the worker. The frontend opens the SSE stream with
    ``?afterSequence=<n>`` so the terminal events the worker writes in the
    POST→SSE gap are replayed instead of lost (the same race
    ``AgentConversationRuntime.resumeAfterEventSequence`` closes on restore).
    """

    conversation: AgentConversationResponse
    userMessage: AgentMessageResponse
    streamUrl: str
    resumeAfterEventSequence: int


class AgentMessageEnvelope(_Contract):
    userMessage: AgentMessageResponse
    streamUrl: str
    resumeAfterEventSequence: int


class AgentInteractionResponse(_Contract):
    id: UUID
    type: str
    status: str
    conversationId: UUID
    executionId: UUID
    candidates: list[dict[str, JSONValue]]
    draft: dict[str, JSONValue] | None = None
    expiresAt: datetime


class AgentInteractionRespondRequest(StrictRequestModel):
    action: str = Field(pattern=r"^(SELECT|MANUAL_INPUT|CONFIRM|CANCEL)$")
    projectId: UUID | None = None
    projectName: str | None = Field(default=None, min_length=1, max_length=100)
    finalFields: dict[str, JSONValue] | None = None

    @model_validator(mode="after")
    def strict_action_payload(self) -> AgentInteractionRespondRequest:
        if self.action == "SELECT" and (
            self.projectId is None or self.projectName is not None or self.finalFields is not None
        ):
            raise ValueError("SELECT requires only projectId")
        if self.action == "MANUAL_INPUT" and (
            self.projectName is None or self.projectId is not None or self.finalFields is not None
        ):
            raise ValueError("MANUAL_INPUT requires only projectName")
        if self.action == "CONFIRM" and (
            self.projectId is not None or self.projectName is not None or self.finalFields is None
        ):
            raise ValueError("CONFIRM requires only finalFields")
        if self.action == "CANCEL" and (
            self.projectId is not None
            or self.projectName is not None
            or self.finalFields is not None
        ):
            raise ValueError("CANCEL does not accept a project")
        return self


class AgentInteractionRespondResponse(_Contract):
    interaction: AgentInteractionResponse
    streamUrl: str | None
    # The SSE sequence baseline for the durable execution this response starts
    # (PROJECT_SELECTION SELECT / MANUAL_INPUT). It is
    # ``conversation.lastEventSequence`` snapshotted in the same transaction
    # that enqueues the resumed task, BEFORE that task is visible to the
    # worker — so the frontend opens the stream with ``?afterSequence=<n>``
    # and the terminal events the worker writes in the POST→SSE gap are
    # replayed instead of lost. ``0`` (the default) is returned by the
    # WRITE_CONFIRMATION / interaction-CANCEL paths that start no execution
    # (``streamUrl is None``); the frontend ignores it then.
    resumeAfterEventSequence: int = 0


class MutationProposalRequest(StrictRequestModel):
    """Wire-neutral proposal payload; fields are checked again by the domain service."""

    projectId: UUID
    riskId: UUID | None = None
    todoId: UUID | None = None
    title: str | None = Field(default=None, max_length=250)
    description: str | None = Field(default=None, max_length=4000)
    level: str | None = None
    category: UUID | None = None
    evidence: str | None = Field(default=None, max_length=10000)
    suggestion: str | None = Field(default=None, max_length=10000)
    resolutionReason: str | None = Field(default=None, max_length=2000)
    urgency: str | None = None
    assigneeUserId: UUID | None = None
    dueDate: str | None = None
    status: str | None = None
    completionNote: str | None = Field(default=None, max_length=2000)
    targetStatus: str | None = None
    batchId: str | None = None


class MutationProposalResponse(_Contract):
    draftId: UUID
    interactionId: UUID
    operation: str
    status: str
    draft: dict[str, JSONValue]


class MutationCommitItem(_Contract):
    draftId: UUID
    success: bool
    code: str
    resourceType: str | None = None
    resourceId: UUID | None = None


class MutationCommitResponse(_Contract):
    status: str
    items: list[MutationCommitItem]


class AgentMessageRequest(StrictRequestModel):
    message: str = Field(min_length=1, max_length=4000)


class AgentConversationRuntime(_Contract):
    """Owner-scoped snapshot of the conversation's live execution.

    Restored by ``history`` so a refresh reattaches to a RUNNING stream or
    re-displays an OPEN interaction instead of forcing a re-send.  Absent
    (``None``) when no execution is active, which keeps the happy-path restore
    (``status == "completed"``) unchanged.

    ``resumeAfterEventSequence`` is ``conversation.lastEventSequence`` at snapshot
    time — always present (a fresh conversation is ``0``).  The restore MUST
    reconnect the stream with this sequence cursor (``?afterSequence=<n>``), NOT
    ``null`` and NOT the event-id cursor: when the worker writes the terminal
    MESSAGE_DELTA/COMPLETED events in the gap between the history response and the
    SSE GET, ``after=None`` re-reads the conversation tail at request time and the
    stream opens *after* those events, observes a terminal task, and closes with
    no event — the UI goes ``disconnected`` and the assistant answer is lost.
    The sequence cursor is always defined even on a brand-new first turn that has
    written zero events (where the event-id cursor is ``None`` and cannot be
    used), so it is the only cursor that closes the zero-event race.  Resuming
    from the snapshot sequence replays exactly the events written in that gap.
    ``resumeAfterEventId`` is retained for a manual reconnect that already holds
    a durable event id; the restore path prefers the sequence cursor.

    ``cancellationRequested`` mirrors the active ``AgentExecutionConfig``'s
    ``cancellationRequestedAt`` (set atomically by ``POST /cancel`` and observed
    by the worker at its next heartbeat boundary).  It is ``True`` while the
    worker is still ``RUNNING`` after an explicit cancel — the runtime ``status``
    stays ``RUNNING`` because the closed ``AgentExecution.status`` enum has no
    ``CANCELLING`` value (ADR 0036), so this boolean is how a restore avoids
    reopening the normal stream and re-enabling the input while the turn is still
    draining to a terminal status.
    """

    status: str
    streamUrl: str | None = None
    interaction: AgentInteractionResponse | None = None
    resumeAfterEventId: UUID | None = None
    resumeAfterEventSequence: int = 0
    cancellationRequested: bool = False


class AgentConversationHistory(_Contract):
    conversation: AgentConversationResponse
    messages: list[AgentMessageResponse]
    nextMessageSequence: int
    runtime: AgentConversationRuntime | None = None


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
    externalCode: str | None = None
    departmentName: str | None = None
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
    externalCode: str | None = None
    departmentName: str | None = None
    status: str


class RiskCategoryListToolResponse(_Contract):
    items: list[dict[str, str]]


class AgentRiskListItem(_Contract):
    """Agent-specific compact risk projection for ``risk_list``.

    The formal ``RiskItem`` (``risks.schemas``) carries ``description``,
    ``evidence`` and ``suggestion`` — long fields that are rarely needed to
    answer a list-shaped question and blow the tool-result budget under the
    effective model context.  This projection keeps only the fields needed to
    *answer* 当前有哪些高风险 (id, project, title, level, status, category,
    owner, timestamps) plus the optional amount columns; ``risk_detail``
    expands the full record.
    """

    id: UUID
    projectId: UUID
    projectName: str
    title: str
    level: ProjectRiskLevel
    status: RiskStatus
    categoryName: str
    departmentName: str | None = None
    projectOwnerName: str | None = None
    actualCollectedAmountYuan: str | None = None
    remainingAmountYuan: str | None = None
    detectedAt: str
    updatedAt: str


class AgentRiskListPage(_Contract):
    items: list[AgentRiskListItem]
    page: int
    pageSize: int
    total: int


class DashboardFocusToolResponse(_Contract):
    """Agent-specific envelope for the dashboard service's list return value."""

    items: list[AgentRiskListItem]


class RiskToolArguments(StrictRequestModel):
    """``risk_list`` contract with deterministic server-side time ranges.

    ``timeRange`` is the closed preset enum: the model only *selects* the
    relative-time semantics (本周/上周/最近7天/本月/上个月); the server resolves
    it into a half-open ``[start, end)`` window on ``Risk.detectedAt`` in the
    business timezone (Asia/Shanghai) — see ``shared.time_ranges``.  Explicit
    ``detectedFrom``/``detectedTo`` (both required together, timezone-aware,
    ``detectedFrom < detectedTo``) express an absolute window instead; the
    preset and the explicit window are mutually exclusive.  All three fields
    are optional, so the legacy argument shapes (e.g. ``{"level": "HIGH"}``)
    keep working unchanged.
    """

    keyword: str | None = Field(default=None, max_length=100)
    level: ProjectRiskLevel | None = None
    status: RiskStatus | None = None
    projectId: UUID | None = None
    timeRange: RiskTimeRangePreset | None = None
    detectedFrom: datetime | None = None
    detectedTo: datetime | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def strict_time_range(self) -> RiskToolArguments:
        if self.timeRange is not None and (
            self.detectedFrom is not None or self.detectedTo is not None
        ):
            raise ValueError("timeRange 与 detectedFrom/detectedTo 互斥, 只能二选一")
        if (self.detectedFrom is None) != (self.detectedTo is None):
            raise ValueError("detectedFrom 与 detectedTo 必须同时提供")
        for name, value in (("detectedFrom", self.detectedFrom), ("detectedTo", self.detectedTo)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} 必须携带时区信息")
        if (
            self.detectedFrom is not None
            and self.detectedTo is not None
            and self.detectedFrom >= self.detectedTo
        ):
            raise ValueError("detectedFrom 必须早于 detectedTo")
        return self


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
    "AgentConversationListItem",
    "AgentConversationListPage",
    "AgentConversationResponse",
    "AgentConversationRuntime",
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
    "MutationCommitItem",
    "MutationCommitResponse",
    "MutationProposalRequest",
    "MutationProposalResponse",
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
