from __future__ import annotations

# ruff: noqa: RUF001
import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.agent.context import (
    ActiveProject,
    AgentConversationContext,
    ConversationContextPolicy,
    ConversationContextService,
    ConversationMessage,
    Summarizer,
    _Turn,
    is_contextual_followup,
    refers_to_active_project,
)
from risk_platform.agent.core import ContextBudget, ReadOnlyAgentCore
from risk_platform.agent.models import AgentConversation, AgentMessage, AgentMessageRole
from risk_platform.agent.schemas import AgentToolResult
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.ai_providers.v2_adapter import (
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorClassification,
    ProviderFinishReason,
    ProviderRole,
    ProviderTokenUsage,
    ProviderToolCall,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.projects.query_service import ProjectsQueryService
from risk_platform.shared.errors import ApiError


def _identity(owner: UUID | None = None) -> SessionIdentity:
    return SessionIdentity(
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=AuthenticatedUser(
            id=str(owner or uuid4()),
            username="context",
            displayName="Context",
            departmentName=None,
            roleCodes=[],
            permissions=["agent.use", "dashboard.view"],
            dataScope="ALL",
            mustChangePassword=False,
        ),
    )


class _Runtime:
    def __init__(self, responses: list[ProviderChatResponse] | None = None) -> None:
        self.requests: list[ProviderChatRequest] = []
        self.responses = responses or []

    async def candidate_snapshot(self) -> tuple[object, ...]:
        return ()

    async def chat_snapshot(
        self, _snapshot: tuple[object, ...], request: ProviderChatRequest
    ) -> ProviderChatResponse:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return ProviderChatResponse(
            content="完成",
            tool_calls=(),
            finish_reason=ProviderFinishReason.STOP,
            usage=ProviderTokenUsage(1, 1, 2),
            latency_ms=1,
        )


class _Tools:
    def catalogue(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    async def invoke(self, *_args: object, **_kwargs: object) -> AgentToolResult:
        raise AssertionError("tool must not be called")


def _response(
    *calls: ProviderToolCall, text: str | None = None
) -> ProviderChatResponse:
    return ProviderChatResponse(
        content=text,
        tool_calls=calls,
        finish_reason=ProviderFinishReason.TOOL_CALLS if calls else ProviderFinishReason.STOP,
        usage=ProviderTokenUsage(1, 1, 2),
        latency_ms=1,
    )


def _message(
    conversation_id: UUID, sequence: int, role: AgentMessageRole, text: str
) -> AgentMessage:
    return AgentMessage(
        id=uuid4(),
        conversationId=conversation_id,
        sequence=sequence,
        role=role,
        content=text,
        traceId="trace",
        createdAt=datetime.now(UTC),
    )


class _MemoryContextService(ConversationContextService):
    def __init__(
        self,
        conversation: AgentConversation,
        messages: list[AgentMessage],
        summarizer: Summarizer,
        policy: ConversationContextPolicy,
    ) -> None:
        super().__init__(
            cast(async_sessionmaker[AsyncSession], None),
            summarizer,
            policy,
        )
        self.conversation = conversation
        self.messages = messages
        self.cas_updates: list[int] = []

    async def _load(
        self, conversation_id: UUID, current_message_id: UUID, owner_id: UUID
    ) -> tuple[AgentConversation, AgentMessage, list[_Turn]]:
        del conversation_id, owner_id
        current = next(item for item in self.messages if item.id == current_message_id)
        eligible = [
            item
            for item in self.messages
            if self.conversation.contextSummaryThroughSequence < item.sequence < current.sequence
        ]
        return self.conversation, current, self._completed_turns(eligible)

    async def _active_project(
        self, conversation: AgentConversation, identity: SessionIdentity
    ) -> ActiveProject | None:
        del conversation, identity
        return None

    async def _compare_and_set_summary(
        self,
        conversation_id: UUID,
        *,
        expected_version: int,
        expected_through: int,
        new_summary: str,
        new_through: int,
    ) -> bool:
        del conversation_id
        if (
            self.conversation.contextSummaryVersion != expected_version
            or self.conversation.contextSummaryThroughSequence != expected_through
        ):
            return False
        self.conversation.contextSummary = new_summary
        self.conversation.contextSummaryThroughSequence = new_through
        self.conversation.contextSummaryVersion += 1
        self.cas_updates.append(new_through)
        return True


def _conversation_fixture(turns: int, content_size: int = 20) -> tuple[
    AgentConversation, list[AgentMessage], AgentMessage
]:
    conversation_id, owner_id = uuid4(), uuid4()
    conversation = AgentConversation(
        id=conversation_id,
        ownerUserId=owner_id,
        createdAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
        expiresAt=datetime.now(UTC) + timedelta(days=90),
        retentionConfigVersion="v1",
        lastMessageSequence=turns * 2 + 1,
        lastEventSequence=0,
        contextSummary=None,
        contextSummaryThroughSequence=0,
        contextSummaryVersion=0,
        activeProjectId=None,
        activeProjectName=None,
    )
    messages: list[AgentMessage] = []
    for index in range(turns):
        messages.extend(
            (
                _message(conversation_id, index * 2 + 1, AgentMessageRole.USER, "U" * content_size),
                _message(
                    conversation_id,
                    index * 2 + 2,
                    AgentMessageRole.ASSISTANT,
                    "A" * content_size,
                ),
            )
        )
    current = _message(
        conversation_id, turns * 2 + 1, AgentMessageRole.USER, "这个项目有什么待办？"
    )
    messages.append(current)
    return conversation, messages, current


def test_provider_request_orders_summary_recent_and_current_message() -> None:
    runtime = _Runtime()
    context = AgentConversationContext(
        summary="较早选择了南岸项目；风险数量只是历史信息。",
        recent_messages=(
            ConversationMessage(3, ProviderRole.USER, "当前有哪些高风险？"),
            ConversationMessage(4, ProviderRole.ASSISTANT, "第一项...第二项..."),
        ),
        active_project=ActiveProject(uuid4(), "南岸项目"),
        summarized_through_sequence=2,
    )
    asyncio.run(
        ReadOnlyAgentCore(
            cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, _Tools())
        ).run(_identity(), "第二个展开说一下", conversation_context=context, candidate_snapshot=())
    )
    request = runtime.requests[0]
    assert [item.role for item in request.messages] == [
        ProviderRole.SYSTEM,
        ProviderRole.SYSTEM,
        ProviderRole.USER,
        ProviderRole.ASSISTANT,
        ProviderRole.USER,
    ]
    assert request.messages[-1].content == "第二个展开说一下"
    assert "必须重新调用授权 tool" in (request.messages[0].content or "")
    assert "不可信业务事实" in (request.messages[1].content or "")


def test_context_compresses_only_old_complete_turns_and_is_monotonic() -> None:
    conversation, messages, current = _conversation_fixture(5, content_size=80)
    calls: list[str] = []

    async def summarize(_snapshot: object, _summary: str | None, transcript: str) -> str:
        calls.append(transcript)
        return "用户讨论南岸项目；旧业务数量不可作为当前事实。"

    service = _MemoryContextService(
        conversation,
        messages,
        summarize,
        ConversationContextPolicy(
            history_budget=600,
            compression_trigger=500,
            compression_target=200,
            summary_input_budget=10_000,
        ),
    )
    result = asyncio.run(service.build(conversation.id, current.id, _identity(), ()))
    assert len(calls) == 1
    assert service.cas_updates == [6]
    assert sum(len((item.content or "").encode()) for item in result.recent_messages) + len(
        (result.summary or "").encode()
    ) <= 600
    assert result.summarized_through_sequence == 6
    assert [item.sequence for item in result.recent_messages] == [7, 8, 9, 10]
    assert current.content not in calls[0]

    asyncio.run(service.build(conversation.id, current.id, _identity(), ()))
    assert len(calls) == 1
    assert service.cas_updates == [6]


def test_compression_failure_degrades_to_latest_complete_turns() -> None:
    conversation, messages, current = _conversation_fixture(4, content_size=100)

    async def fail(*_args: object) -> str:
        raise ProviderError(
            ProviderErrorClassification.NETWORK,
            retryable=True,
            failover_allowed=False,
        )

    service = _MemoryContextService(
        conversation,
        messages,
        fail,
        ConversationContextPolicy(
            history_budget=500,
            compression_trigger=300,
            compression_target=100,
        ),
    )
    result = asyncio.run(service.build(conversation.id, current.id, _identity(), ()))
    assert result.summary is None
    assert [item.sequence for item in result.recent_messages] == [5, 6, 7, 8]
    assert conversation.contextSummaryVersion == 0


def test_context_budget_is_unified_and_active_project_reference_is_explicit() -> None:
    budget = ContextBudget()
    assert budget.history_budget == 16 * 1024
    assert budget.tool_result_reserve < budget.hard_context_budget
    assert refers_to_active_project("这个项目有什么风险？") is True
    assert refers_to_active_project("B 项目有什么风险？") is False
    assert is_contextual_followup("按你上面的建议创建网页") is False


def test_below_trigger_does_not_call_summarizer() -> None:
    conversation, messages, current = _conversation_fixture(2, content_size=10)
    calls = 0

    async def summarize(*_args: object) -> str:
        nonlocal calls
        calls += 1
        return "unused"

    service = _MemoryContextService(
        conversation,
        messages,
        summarize,
        ConversationContextPolicy(
            history_budget=1_000,
            compression_trigger=900,
            compression_target=300,
        ),
    )
    result = asyncio.run(service.build(conversation.id, current.id, _identity(), ()))
    assert calls == 0
    assert [item.sequence for item in result.recent_messages] == [1, 2, 3, 4]


def test_summarizer_request_has_no_tools() -> None:
    runtime = _Runtime()
    summary = asyncio.run(
        ReadOnlyAgentCore(
            cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, _Tools())
        ).summarize_conversation((), None, "USER: earlier\nASSISTANT: answer")
    )
    assert summary == "完成"
    assert runtime.requests[0].tools == ()
    assert [item.role for item in runtime.requests[0].messages] == [
        ProviderRole.SYSTEM,
        ProviderRole.USER,
    ]


def test_unique_authorized_project_search_refreshes_active_project() -> None:
    project_id = uuid4()
    runtime = _Runtime(
        [
            _response(ProviderToolCall("search-1", "project_search", {"query": "B"})),
            _response(text="已切换到 B 项目"),
        ]
    )

    class _ProjectTools(_Tools):
        def catalogue(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
            return [
                {"name": "project_search", "description": "search", "argumentsSchema": {}}
            ]

        async def invoke(self, *_args: object, **_kwargs: object) -> AgentToolResult:
            return AgentToolResult(
                toolInvocationId="search-1",
                tool="project_search",
                data={"total": 1, "items": [{"id": str(project_id), "name": "B 项目"}]},
                dataAsOf=datetime.now(UTC),
                traceId="trace",
                provenance="test",
            )

    outcome = asyncio.run(
        ReadOnlyAgentCore(
            cast(ProviderV2Runtime, runtime), cast(AgentToolRegistry, _ProjectTools())
        ).run(
            _identity(),
            "不是 A，我说的是 B",
            conversation_context=AgentConversationContext(
                summary=None,
                recent_messages=(
                    ConversationMessage(1, ProviderRole.USER, "A 项目有什么风险"),
                    ConversationMessage(2, ProviderRole.ASSISTANT, "已查询 A 项目"),
                ),
                active_project=None,
                summarized_through_sequence=0,
            ),
            candidate_snapshot=(),
        )
    )
    assert outcome.active_project == ActiveProject(project_id, "B 项目")


class _ProjectReader:
    def __init__(self, error: ApiError | None = None) -> None:
        self.error = error

    async def detail(self, _identity: SessionIdentity, project_id: UUID) -> object:
        if self.error is not None:
            raise self.error
        return type("Project", (), {"id": project_id, "name": "南岸项目"})()


class _LockSession:
    def __init__(self, conversation: AgentConversation) -> None:
        self.conversation = conversation

    async def scalar(self, _query: object) -> AgentConversation:
        return self.conversation


class _Begin:
    def __init__(self, conversation: AgentConversation) -> None:
        self.session = _LockSession(conversation)

    async def __aenter__(self) -> _LockSession:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Sessions:
    def __init__(self, conversation: AgentConversation) -> None:
        self.conversation = conversation

    def begin(self) -> _Begin:
        return _Begin(self.conversation)


def test_active_project_is_revalidated_and_cleared_after_scope_revocation() -> None:
    conversation, messages, _ = _conversation_fixture(0)
    project_id = uuid4()
    conversation.activeProjectId = project_id
    conversation.activeProjectName = "旧名称"
    del messages
    service = ConversationContextService(
        cast(async_sessionmaker[AsyncSession], _Sessions(conversation)),
        cast(Summarizer, None),
        ConversationContextPolicy(1_000, 900, 300),
    )
    service._projects = cast(ProjectsQueryService, _ProjectReader())
    active = asyncio.run(service._active_project(conversation, _identity()))
    assert active == ActiveProject(project_id, "南岸项目")

    service._projects = cast(
        ProjectsQueryService,
        _ProjectReader(ApiError(404, "PROJECT_NOT_FOUND", "not found")),
    )
    service._sessions = cast(async_sessionmaker[AsyncSession], _Sessions(conversation))
    assert asyncio.run(service._active_project(conversation, _identity())) is None
    assert conversation.activeProjectId is None
    assert conversation.activeProjectName is None
