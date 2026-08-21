"""Transactional administration of Agent layer-1 runtime scope rules."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.agent_scope.schemas import (
    PREVIEW_CANDIDATE_LABEL,
    CreateScopeRuleRequest,
    ScopeRuleCandidateRule,
    ScopeRuleDecisionValue,
    ScopeRuleResponse,
    ScopeRuleTestMatch,
    ScopeRuleTestRequest,
    ScopeRuleTestResponse,
    ScopeRuleWarning,
    UpdateScopeRuleRequest,
)
from risk_platform.admin.agent_scope.warnings import analyze_scope_rule_warnings
from risk_platform.agent.models import (
    AgentScopeRule,
    AgentScopeRuleDecision,
    AgentScopeRuleRevision,
)
from risk_platform.agent.scope import ScopeDecision, ScopeEvaluation, ScopeRuleMatchType
from risk_platform.agent.scope_rules import (
    CompiledScopeRule,
    ScopeRuleStore,
    compile_scope_rule,
    compose_preview_snapshot,
    evaluate_with_snapshot,
)
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.shared.errors import ApiError


class AdminAgentScopeRulesService:
    """CRUD plus live-effect testing for the dynamic layer-1 scope rules.

    Every mutation commits together with an atomic ``revision + 1`` on the
    single-row revision table, then notifies the process-local rule cache so
    the change takes effect immediately (other instances follow via the Redis
    invalidation event or the TTL poll).  Deletes are soft (``deletedAt``) to
    preserve the security-configuration history.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        store: ScopeRuleStore,
    ) -> None:
        self._session_factory = session_factory
        self._store = store

    async def list_rules(self) -> list[ScopeRuleResponse]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(AgentScopeRule)
                    .where(AgentScopeRule.deletedAt.is_(None))
                    .order_by(AgentScopeRule.priority.desc(), AgentScopeRule.createdAt.asc())
                )
            ).all()
            return [_map_rule(row) for row in rows]

    async def create(
        self, payload: CreateScopeRuleRequest, identity: SessionIdentity, trace_id: UUID
    ) -> ScopeRuleResponse:
        response: ScopeRuleResponse | None = None
        async with self._mutation_transaction(identity, trace_id, "ADMIN_SCOPE_RULE_CREATED") as (
            session
        ):
            name = payload.name.strip()
            await self._ensure_name_free(session, name)
            rule = AgentScopeRule(
                name=name,
                decision=AgentScopeRuleDecision(payload.decision),
                matchType=ScopeRuleMatchType(payload.matchType),
                pattern=payload.pattern.strip(),
                priority=payload.priority,
                enabled=payload.enabled,
                description=_normalize_optional(payload.description),
                createdBy=UUID(identity.user.id),
            )
            session.add(rule)
            await session.flush()  # populate rule.id before the audit record
            await self._bump_revision(session)
            await self._audit(session, identity, trace_id, "ADMIN_SCOPE_RULE_CREATED", rule.id)
            response = _map_rule(rule)
        await self._store.notify_changed()
        assert response is not None
        return response

    async def update(
        self,
        rule_id: UUID,
        payload: UpdateScopeRuleRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> ScopeRuleResponse:
        response: ScopeRuleResponse | None = None
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_SCOPE_RULE_UPDATED", rule_id
        ) as session:
            rule = await self._rule_or_error(session, rule_id)
            if rule.version != payload.version:
                raise ApiError(409, "CONFLICT", "规则已被他人修改，请刷新后重试")
            if payload.name is not None:
                name = payload.name.strip()
                if name != rule.name:
                    await self._ensure_name_free(session, name)
                rule.name = name
            if payload.decision is not None:
                rule.decision = AgentScopeRuleDecision(payload.decision)
            if payload.matchType is not None:
                rule.matchType = ScopeRuleMatchType(payload.matchType)
            if payload.pattern is not None:
                rule.pattern = payload.pattern.strip()
            if payload.priority is not None:
                rule.priority = payload.priority
            if payload.enabled is not None:
                rule.enabled = payload.enabled
            if payload.description is not None:
                rule.description = _normalize_optional(payload.description)
            rule.version += 1
            await self._bump_revision(session)
            await self._audit(session, identity, trace_id, "ADMIN_SCOPE_RULE_UPDATED", rule.id)
            await session.flush()
            response = _map_rule(rule)
        await self._store.notify_changed()
        assert response is not None
        return response

    async def remove(
        self,
        rule_id: UUID,
        version: int,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> None:
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_SCOPE_RULE_DELETED", rule_id
        ) as session:
            rule = await self._rule_or_error(session, rule_id)
            if rule.version != version:
                raise ApiError(409, "CONFLICT", "规则已被他人修改，请刷新后重试")
            rule.deletedAt = datetime.now(UTC)
            rule.version += 1
            await self._bump_revision(session)
            await self._audit(session, identity, trace_id, "ADMIN_SCOPE_RULE_DELETED", rule.id)
            await session.flush()
        await self._store.notify_changed()

    async def test(self, payload: ScopeRuleTestRequest) -> ScopeRuleTestResponse:
        """Evaluate a message, optionally previewing one non-live rule.

        Read-only: no row is written, no ``enabled``/``version``/revision
        changes, no cache swap, no invalidation publish.  A preview combines
        the live snapshot with the candidate using the exact production
        ordering (``compose_preview_snapshot``), so the result is what Layer 1
        *would* decide if the rule were enabled.
        """

        with suppress(Exception):
            # Freshest rules without waiting for the TTL; a PG hiccup here
            # must not break testing — the last good snapshot still evaluates.
            await self._store.probe()
        snapshot = self._store.get_snapshot()
        preview_rule_id: str | None = None
        warnings: list[ScopeRuleWarning] = []
        if payload.ruleId is not None:
            rule = await self._previewable_rule(payload.ruleId)
            candidate = _compile_row(rule)
            preview_rule_id = str(rule.id)
            snapshot = compose_preview_snapshot(
                snapshot, candidate, exclude_rule_id=str(rule.id)
            )
            warnings = _rule_warnings(rule.decision.value, rule.matchType.value, rule.pattern)
        elif payload.candidateRule is not None:
            candidate_rule = payload.candidateRule
            candidate = _compile_candidate(candidate_rule)
            snapshot = compose_preview_snapshot(snapshot, candidate)
            warnings = _rule_warnings(
                candidate_rule.decision, candidate_rule.matchType, candidate_rule.pattern
            )
        evaluation = evaluate_with_snapshot(payload.message, snapshot)
        return _map_evaluation(
            evaluation,
            preview=payload.ruleId is not None or payload.candidateRule is not None,
            preview_rule_id=preview_rule_id,
            warnings=warnings,
        )

    async def _previewable_rule(self, rule_id: UUID) -> AgentScopeRule:
        """Load one non-deleted rule for preview; ``enabled`` may be false."""

        async with self._session_factory() as session:
            rule = await session.scalar(
                select(AgentScopeRule).where(
                    AgentScopeRule.id == rule_id, AgentScopeRule.deletedAt.is_(None)
                )
            )
        if rule is None:
            raise ApiError(404, "NOT_FOUND", "范围规则不存在")
        return rule

    async def _rule_or_error(self, session: AsyncSession, rule_id: UUID) -> AgentScopeRule:
        rule = await session.scalar(
            select(AgentScopeRule)
            .where(AgentScopeRule.id == rule_id, AgentScopeRule.deletedAt.is_(None))
            .with_for_update()
        )
        if rule is None:
            raise ApiError(404, "NOT_FOUND", "范围规则不存在")
        return rule

    async def _ensure_name_free(self, session: AsyncSession, name: str) -> None:
        existing = await session.scalar(
            select(AgentScopeRule.id).where(
                AgentScopeRule.name == name, AgentScopeRule.deletedAt.is_(None)
            )
        )
        if existing is not None:
            raise ApiError(409, "CONFLICT", "同名范围规则已存在")

    async def _bump_revision(self, session: AsyncSession) -> None:
        """Atomically bump the single-row revision, self-healing a missing row.

        A plain ``UPDATE ... SET revision = revision + 1`` silently affects
        zero rows when the revision row is missing, after which every cache
        stops noticing changes forever.  The upsert guarantees the row exists
        and moves monotonically within the caller's mutation transaction.
        """

        await session.execute(
            pg_insert(AgentScopeRuleRevision)
            .values(id=1, revision=1)
            .on_conflict_do_update(
                index_elements=[AgentScopeRuleRevision.id],
                set_={"revision": AgentScopeRuleRevision.revision + 1},
            )
        )

    async def _audit(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        rule_id: UUID,
    ) -> None:
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="ADMIN_AGENT_SCOPE",
            action=action,
            resource_type="AGENT_SCOPE_RULE",
            resource_id=str(rule_id),
            trace_id=trace_id,
        )

    @asynccontextmanager
    async def _mutation_transaction(
        self,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        rule_id: UUID | None = None,
    ) -> AsyncIterator[AsyncSession]:
        try:
            async with transaction(self._session_factory) as session:
                yield session
        except ApiError as error:
            async with transaction(self._session_factory) as audit_session:
                await AuditService(audit_session).record_failure(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="ADMIN_AGENT_SCOPE",
                    action=action,
                    resource_type="AGENT_SCOPE_RULE",
                    resource_id=str(rule_id) if rule_id is not None else None,
                    trace_id=trace_id,
                    failure_code=error.code,
                )
            raise


def _compile_row(rule: AgentScopeRule) -> CompiledScopeRule:
    return compile_scope_rule(
        rule_id=str(rule.id),
        name=rule.name,
        decision=ScopeDecision(rule.decision.value),
        match_type=rule.matchType,
        priority=rule.priority,
        pattern=rule.pattern,
    )


def _compile_candidate(candidate: ScopeRuleCandidateRule) -> CompiledScopeRule:
    try:
        return compile_scope_rule(
            rule_id="",
            name=PREVIEW_CANDIDATE_LABEL,
            decision=ScopeDecision(candidate.decision),
            match_type=ScopeRuleMatchType(candidate.matchType),
            priority=candidate.priority,
            pattern=candidate.pattern,
        )
    except ValueError as error:
        raise ApiError(422, "VALIDATION_ERROR", "规则模式无效") from error


def _rule_warnings(
    decision: str, match_type: str, pattern: str
) -> list[ScopeRuleWarning]:
    return analyze_scope_rule_warnings(decision, match_type, pattern)


def _map_rule(rule: AgentScopeRule) -> ScopeRuleResponse:
    if rule.createdAt is None or rule.updatedAt is None:
        raise RuntimeError("agent scope rule timestamps were not populated")
    return ScopeRuleResponse(
        id=str(rule.id),
        name=rule.name,
        decision=rule.decision.value,
        matchType=rule.matchType.value,
        pattern=rule.pattern,
        priority=rule.priority,
        enabled=rule.enabled,
        description=rule.description,
        version=rule.version,
        createdBy=str(rule.createdBy) if rule.createdBy is not None else None,
        createdAt=rule.createdAt.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        updatedAt=rule.updatedAt.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        warnings=_rule_warnings(rule.decision.value, rule.matchType.value, rule.pattern),
    )


def _map_evaluation(
    evaluation: ScopeEvaluation,
    *,
    preview: bool = False,
    preview_rule_id: str | None = None,
    warnings: list[ScopeRuleWarning] | None = None,
) -> ScopeRuleTestResponse:
    matched: ScopeRuleTestMatch | None = None
    if evaluation.source.value == "RUNTIME_RULE" and evaluation.match is not None:
        match = evaluation.match
        matched = ScopeRuleTestMatch(
            id=match.rule_id or "",
            name=match.rule_name or "",
            matchType=match.match_type.value if match.match_type is not None else "PHRASE",
            decision=cast(ScopeRuleDecisionValue, evaluation.decision.value),
            priority=match.priority or 0,
        )
    return ScopeRuleTestResponse(
        decision=evaluation.decision.value,
        source=evaluation.source.value,
        matchedRule=matched,
        preview=preview,
        previewRuleId=preview_rule_id,
        warnings=warnings or [],
    )


def _normalize_optional(value: str | None) -> str | None:
    normalized = value.strip() if value else ""
    return normalized or None


__all__ = ["AdminAgentScopeRulesService"]
