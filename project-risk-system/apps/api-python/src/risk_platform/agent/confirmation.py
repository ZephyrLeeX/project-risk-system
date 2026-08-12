"""One-use, actor-bound Agent confirmation transactions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import get_scoped_project
from risk_platform.risks.models import ProjectRiskLevel, Risk, RiskCategory, RiskSourceType
from risk_platform.risks.schemas import LifecycleRequest
from risk_platform.risks.service import RiskCreate, RisksService
from risk_platform.shared.errors import ApiError
from risk_platform.todos.service import TodoProcessCommand, TodosService

from .execution import AgentExecutionWorker
from .models import (
    AgentConfirmationOperation,
    AgentConfirmationToken,
    AgentConversation,
)


class _CanonicalContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["REPORT", "PROCESS", "RESOLVE"]
    projectId: UUID
    riskId: UUID | None
    todoId: UUID | None
    title: str
    description: str
    riskLevel: Literal["HIGH", "MEDIUM", "LOW"] | None
    dueDate: date | None
    assigneeUserId: UUID | None
    categoryId: UUID | None
    categoryBindingDigest: str | None

    @model_validator(mode="after")
    def validate_operation(self) -> _CanonicalContent:
        if self.operation == "REPORT":
            if (
                not self.title.strip()
                or not self.description.strip()
                or self.riskLevel is None
                or self.categoryId is None
                or self.categoryBindingDigest is None
                or self.riskId is not None
                or self.todoId is not None
                or self.dueDate is not None
                or self.assigneeUserId is not None
            ):
                raise ValueError("invalid REPORT canonical content")
        elif self.operation == "PROCESS":
            if (
                self.riskId is None
                or self.todoId is None
                or not self.description.strip()
                or self.title
                or self.riskLevel is not None
                or self.categoryId is not None
                or self.categoryBindingDigest is not None
            ):
                raise ValueError("invalid PROCESS canonical content")
        elif (
            self.riskId is None
            or not self.description.strip()
            or self.todoId is not None
            or self.title
            or self.riskLevel is not None
            or self.dueDate is not None
            or self.assigneeUserId is not None
            or self.categoryId is not None
            or self.categoryBindingDigest is not None
        ):
            raise ValueError("invalid RESOLVE canonical content")
        return self


@dataclass(frozen=True, slots=True)
class _AuditContext:
    token_id: UUID | None = None
    operation: AgentConfirmationOperation | None = None
    project_id: UUID | None = None


class AgentConfirmationService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def confirm(
        self, identity: SessionIdentity, raw_token: str, trace_id: UUID
    ) -> dict[str, object]:
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        audit_context = _AuditContext()
        try:
            async with transaction(self._sessions) as session:
                token = await self._token(session, digest)
                audit_context = _AuditContext(token.id, token.operation)
                if token.ownerUserId != UUID(identity.user.id):
                    raise ApiError(
                        403, "AGENT_CONFIRMATION_OWNER_MISMATCH", "确认凭证不属于当前用户"
                    )
                content = self._content(token)
                audit_context = _AuditContext(token.id, token.operation, content.projectId)
                if token.usedAt is not None:
                    if token.resultResourceId is None or token.resultResourceType is None:
                        raise ApiError(
                            409,
                            "AGENT_CONFIRMATION_ALREADY_USED",
                            "确认凭证已被使用",
                        )
                    result = self._result(token)
                    await self._record_success(session, identity, token, result, trace_id)
                    return result
                if token.expiresAt <= datetime.now(UTC):
                    raise ApiError(410, "AGENT_CONFIRMATION_EXPIRED", "确认凭证已过期")
                conversation_owner = await session.scalar(
                    select(AgentConversation.ownerUserId).where(
                        AgentConversation.id == token.conversationId
                    )
                )
                if conversation_owner != UUID(identity.user.id):
                    raise self._mismatch()
                current = await AgentExecutionWorker._identity(session, UUID(identity.user.id))
                scope_fact = await AgentExecutionWorker._scope_fact(session, current)
                if self._scope_digest(scope_fact) != token.scopeDigest:
                    raise self._mismatch()
                permission = (
                    "risk.report"
                    if token.operation is AgentConfirmationOperation.REPORT
                    else "risk.resolve"
                )
                if permission not in current.user.permissions:
                    raise self._mismatch()
                project = await get_scoped_project(
                    session,
                    content.projectId,
                    UUID(current.user.id),
                    DataScopeType(current.user.dataScope),
                )
                if project is None:
                    raise self._mismatch()
                try:
                    risk = await self._execute(session, current, token, content, trace_id)
                except ApiError as exc:
                    if exc.code == "RISK_ALREADY_RESOLVED":
                        raise ApiError(
                            409, "AGENT_RISK_ALREADY_RESOLVED", "风险已经解除"
                        ) from None
                    raise self._mismatch() from None
                completed_at = datetime.now(UTC)
                token.usedAt = completed_at.replace(
                    microsecond=(completed_at.microsecond // 1000) * 1000
                )
                token.resultResourceType = "RISK"
                token.resultResourceId = risk.id
                result = self._result(token)
                await self._record_success(session, current, token, result, trace_id)
                await session.flush()
                return result
        except ApiError as exc:
            await self._record_failure(identity, audit_context, trace_id, exc.code)
            raise
        except OperationalError as exc:
            error = ApiError(409, "AGENT_CONFIRMATION_IN_PROGRESS", "确认正在处理中")
            await self._record_failure(identity, audit_context, trace_id, error.code)
            raise error from exc

    async def _token(self, session: AsyncSession, digest: str) -> AgentConfirmationToken:
        try:
            token = await session.scalar(
                select(AgentConfirmationToken)
                .where(AgentConfirmationToken.tokenDigest == digest)
                .with_for_update(nowait=True)
            )
        except OperationalError:
            raise ApiError(409, "AGENT_CONFIRMATION_IN_PROGRESS", "确认正在处理中") from None
        if token is None:
            raise ApiError(409, "AGENT_CONFIRMATION_CONTENT_MISMATCH", "确认凭证无效")
        return token

    @staticmethod
    def _content(token: AgentConfirmationToken) -> _CanonicalContent:
        try:
            raw = json.loads(token.canonicalContent)
            content = _CanonicalContent.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            raise AgentConfirmationService._mismatch() from None
        canonical = AgentExecutionWorker._canonical(content.model_dump(mode="json"))
        if (
            canonical != token.canonicalContent
            or hashlib.sha256(canonical.encode()).hexdigest() != token.contentDigest
            or content.operation != token.operation.value
        ):
            raise AgentConfirmationService._mismatch()
        return content

    async def _execute(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        token: AgentConfirmationToken,
        content: _CanonicalContent,
        trace_id: UUID,
    ) -> Risk:
        if token.operation is AgentConfirmationOperation.REPORT:
            category = await self._category(session, content)
            assert content.riskLevel is not None
            command = RiskCreate(
                project_id=content.projectId,
                category_id=category.id,
                title=content.title,
                description=content.description,
                level=ProjectRiskLevel(content.riskLevel),
                source_type=RiskSourceType.MANUAL,
                dedupe_fingerprint=token.idempotencyKey,
                reporter_user_id=UUID(identity.user.id),
                actor_name=identity.user.displayName,
            )
            return await RisksService(self._sessions).create_in_session(
                session, command, actor_id=UUID(identity.user.id), trace_id=trace_id
            )
        assert content.riskId is not None
        if token.operation is AgentConfirmationOperation.PROCESS:
            assert content.todoId is not None
            await TodosService(self._sessions).process_in_session(
                session,
                identity,
                content.todoId,
                TodoProcessCommand(
                    project_id=content.projectId,
                    risk_id=content.riskId,
                    description=content.description,
                    due_date=content.dueDate,
                    assignee_user_id=content.assigneeUserId,
                ),
                trace_id=trace_id,
            )
            risk = await session.get(Risk, content.riskId)
            if risk is None:
                raise self._mismatch()
            return risk
        row = await RisksService(self._sessions).resolve_in_session(
            session,
            identity,
            content.riskId,
            LifecycleRequest(reason=content.description),
            trace_id,
        )
        return cast(Risk, row[0])

    async def _category(
        self, session: AsyncSession, content: _CanonicalContent
    ) -> RiskCategory:
        if content.categoryId is None or content.categoryBindingDigest is None:
            raise self._mismatch()
        category = await session.scalar(
            select(RiskCategory)
            .where(RiskCategory.id == content.categoryId, RiskCategory.isActive.is_(True))
            .with_for_update(read=True)
        )
        if (
            category is None
            or AgentExecutionWorker._category_binding(category)
            != content.categoryBindingDigest
        ):
            raise self._mismatch()
        return category

    async def _record_success(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        token: AgentConfirmationToken,
        result: dict[str, object],
        trace_id: UUID,
    ) -> None:
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="AGENT",
            action=f"AGENT_{token.operation.value}_CONFIRMED",
            resource_type="RISK",
            resource_id=str(result["resourceId"]),
            trace_id=trace_id,
            request_id=token.id,
            project_id=self._project_id_from_token(token),
        )

    async def _record_failure(
        self,
        identity: SessionIdentity,
        context: _AuditContext,
        trace_id: UUID,
        failure_code: str,
    ) -> None:
        async with transaction(self._sessions) as session:
            await AuditService(session).record_failure(
                actor_id=UUID(identity.user.id),
                actor_type=AuditActorType.USER,
                module="AGENT",
                action=(
                    f"AGENT_{context.operation.value}_CONFIRMED"
                    if context.operation is not None
                    else "AGENT_CONFIRMATION_FAILED"
                ),
                resource_type="AGENT_CONFIRMATION",
                resource_id=str(context.token_id) if context.token_id is not None else None,
                trace_id=trace_id,
                request_id=context.token_id,
                project_id=context.project_id,
                failure_code=failure_code,
            )

    @staticmethod
    def _project_id_from_token(token: AgentConfirmationToken) -> UUID | None:
        try:
            value = json.loads(token.canonicalContent).get("projectId")
            return UUID(value) if isinstance(value, str) else None
        except (AttributeError, json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _scope_digest(value: object) -> str:
        return hashlib.sha256(AgentExecutionWorker._canonical(value).encode()).hexdigest()

    @staticmethod
    def _result(token: AgentConfirmationToken) -> dict[str, object]:
        if (
            token.resultResourceId is None
            or token.resultResourceType is None
            or token.usedAt is None
        ):
            raise ApiError(409, "AGENT_CONFIRMATION_IN_PROGRESS", "确认正在处理中")
        return {
            "operation": token.operation.value,
            "resourceType": token.resultResourceType,
            "resourceId": token.resultResourceId,
            "completedAt": token.usedAt,
        }

    @staticmethod
    def _mismatch() -> ApiError:
        return ApiError(409, "AGENT_CONFIRMATION_CONTENT_MISMATCH", "确认内容或当前授权已变化")


__all__ = ["AgentConfirmationService"]
