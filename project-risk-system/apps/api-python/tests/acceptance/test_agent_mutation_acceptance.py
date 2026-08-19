from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import func, select

from acceptance.conftest import AcceptanceHarness
from risk_platform.agent.core import ReadOnlyAgentCore
from risk_platform.agent.models import (
    AgentExecution,
    AgentInteraction,
    AgentInteractionStatus,
    AgentInteractionType,
    MutationDraft,
)
from risk_platform.agent.mutations import MutationConfirmationRequired
from risk_platform.agent.schemas import AgentToolResult
from risk_platform.agent.service import AgentConversationService
from risk_platform.agent.tools import AgentToolRegistry
from risk_platform.ai_providers.v2_adapter import (
    ProviderChatResponse,
    ProviderFinishReason,
    ProviderTokenUsage,
    ProviderToolCall,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime
from risk_platform.auth.service import SessionIdentity
from risk_platform.dashboard.service import DashboardService
from risk_platform.risks.models import Risk
from risk_platform.risks.service import RisksService
from risk_platform.todos.service import TodosService
from risk_platform.weekly_reports.service import WeeklyReportService


class _ProposalRuntime:
    project_id: str
    category_id: str

    async def candidate_snapshot(self) -> tuple[object, ...]:
        return ()

    async def chat_snapshot(
        self, _snapshot: tuple[object, ...], _request: object
    ) -> ProviderChatResponse:
        return ProviderChatResponse(
            content=None,
            tool_calls=(
                ProviderToolCall(
                    "risk-create-1",
                    "risk_create_proposal",
                    {
                        "projectId": self.project_id,
                        "category": self.category_id,
                        "title": "付款风险",
                        "description": "甲方临近付款期也一直没有付项目款",
                    },
                ),
            ),
            finish_reason=ProviderFinishReason.TOOL_CALLS,
            usage=ProviderTokenUsage(1, 1, 2),
            latency_ms=1,
        )


class _ProposalTools:
    def __init__(self, registry: AgentToolRegistry, runtime: _ProposalRuntime) -> None:
        self.registry = registry
        self.runtime = runtime

    def catalogue(
        self,
        identity: SessionIdentity,
        *,
        selected_project_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        return self.registry.catalogue(identity, selected_project_id=selected_project_id)

    async def invoke(
        self,
        identity: SessionIdentity,
        name: str,
        arguments: object,
        *,
        trace_id: str,
        mutation_context: tuple[UUID, UUID] | None = None,
    ) -> AgentToolResult:
        assert mutation_context is not None
        return await self.registry.invoke(
            identity,
            name,
            cast(dict[str, object], arguments),
            trace_id=trace_id,
            mutation_context=mutation_context,
        )


def test_model_proposal_creates_draft_and_confirmation_without_risk_write(
    acceptance: AcceptanceHarness,
) -> None:
    async def run() -> None:
        identity = acceptance.full_identity()
        project_id = acceptance.env.seed.projects["owned"]
        category_id = acceptance.env.seed.category_id
        assert category_id is not None
        service = AgentConversationService(acceptance.env.factory)
        created = await service.create(
            identity,
            "我要上报一个erp系统的风险\uFF0C甲方临近付款期也一直没有付项目款",
        )
        async with acceptance.env.factory() as session:
            execution = await session.scalar(
                select(AgentExecution).where(
                    AgentExecution.conversationId == created.conversation.id
                )
            )
            before_risks = await session.scalar(select(func.count()).select_from(Risk))
        assert execution is not None

        runtime = _ProposalRuntime()
        runtime.project_id = str(project_id)
        runtime.category_id = str(category_id)
        registry = AgentToolRegistry(
            acceptance.env.factory,
            DashboardService(acceptance.env.factory),
            RisksService(acceptance.env.factory),
            TodosService(acceptance.env.factory),
            WeeklyReportService(acceptance.env.factory),
        )
        with pytest.raises(MutationConfirmationRequired):
            await ReadOnlyAgentCore(
                cast(ProviderV2Runtime, runtime),
                cast(AgentToolRegistry, _ProposalTools(registry, runtime)),
            ).run(
                identity,
                "我要上报一个erp系统的风险\uFF0C甲方临近付款期也一直没有付项目款",
                conversation_id=created.conversation.id,
                execution_id=execution.id,
            )

        async with acceptance.env.factory() as session:
            after_risks = await session.scalar(select(func.count()).select_from(Risk))
            draft = await session.scalar(
                select(MutationDraft).where(MutationDraft.executionId == execution.id)
            )
            interaction = await session.scalar(
                select(AgentInteraction).where(
                    AgentInteraction.executionId == execution.id,
                    AgentInteraction.type == AgentInteractionType.WRITE_CONFIRMATION,
                )
            )
        assert after_risks == before_risks
        assert draft is not None and draft.status.value == "OPEN"
        assert interaction is not None
        assert interaction.status is AgentInteractionStatus.OPEN

    asyncio.run(run())
