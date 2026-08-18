"""Bounded, server-authoritative project resolution shared by mail and Agent callers."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from risk_platform.projects.models import Project, ProjectAlias, ProjectStatus
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate

MAX_PROJECT_RESOLUTION_CANDIDATES = 20
MAX_RESOLUTION_TEXT = 8_000
MIN_TOKEN_LENGTH = 2


def normalize(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value.casefold(), flags=re.UNICODE)


def search_tokens(subject: str, body: str) -> tuple[str, ...]:
    text = f"{subject}\n{body}"[:MAX_RESOLUTION_TEXT]
    # Keep meaningful alphanumeric/CJK runs. The query remains bounded and the
    # mail body is never sent to SQL or the provider without this limit.
    tokens = re.findall(r"[\w\u3400-\u9fff]{2,}", text.casefold(), flags=re.UNICODE)
    return tuple(
        dict.fromkeys(token for token in tokens if len(normalize(token)) >= MIN_TOKEN_LENGTH)
    )


@dataclass(frozen=True, slots=True)
class ResolutionCandidate:
    option_id: str
    project_id: UUID
    name: str
    external_code: str | None
    alias: str | None
    status: str


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    decision: Literal["AUTO_MATCH", "MANUAL_REVIEW"]
    project_id: UUID | None
    confidence: int | None
    candidates: tuple[ResolutionCandidate, ...]
    source: Literal["DETERMINISTIC", "AI", "NONE"]


Provider = Callable[[dict[str, object]], Awaitable[str]]


class ProjectResolutionService:
    """Resolve only against rows selected by this server-side bounded query."""

    def __init__(self, max_candidates: int = MAX_PROJECT_RESOLUTION_CANDIDATES) -> None:
        if not 1 <= max_candidates <= MAX_PROJECT_RESOLUTION_CANDIDATES:
            raise ValueError("max_candidates must be between 1 and 20")
        self.max_candidates = max_candidates

    async def retrieve_candidates(
        self,
        session: AsyncSession,
        subject: str,
        body: str,
        user_id: UUID,
        data_scope: DataScopeType | str,
    ) -> tuple[ResolutionCandidate, ...]:
        tokens = search_tokens(subject, body)
        if not tokens:
            return ()
        fields: list[ColumnElement[bool]] = []
        for token in tokens[:12]:
            pattern = f"%{token}%"
            fields.extend(
                (
                    Project.name.ilike(pattern),
                    Project.externalCode.ilike(pattern),
                    Project.alias.ilike(pattern),
                    select(ProjectAlias.id)
                    .where(
                        ProjectAlias.projectId == Project.id,
                        ProjectAlias.isActive.is_(True),
                        ProjectAlias.alias.ilike(pattern),
                    )
                    .exists(),
                )
            )
        active_alias = (
            select(ProjectAlias.alias)
            .where(ProjectAlias.projectId == Project.id, ProjectAlias.isActive.is_(True))
            .order_by(ProjectAlias.alias, ProjectAlias.id)
            .limit(1)
            .scalar_subquery()
        )
        rows = (
            await session.execute(
                select(Project)
                .add_columns(active_alias)
                .where(
                    project_scope_predicate(user_id, data_scope),
                    Project.status != ProjectStatus.ARCHIVED,
                    or_(*fields),
                )
                .order_by(Project.name, Project.id)
                .limit(self.max_candidates)
            )
        ).all()
        return tuple(
            ResolutionCandidate(
                option_id=f"P{index}",
                project_id=project.id,
                name=project.name,
                external_code=project.externalCode,
                alias=project.alias or active_alias_value,
                status=project.status.value,
            )
            for index, (project, active_alias_value) in enumerate(rows, 1)
        )

    async def resolve(
        self,
        session: AsyncSession,
        subject: str,
        body: str,
        user_id: UUID,
        data_scope: DataScopeType | str,
        provider: Provider | None = None,
    ) -> ResolutionResult:
        candidates = await self.retrieve_candidates(session, subject, body, user_id, data_scope)
        normalized_mail = normalize(f"{subject}\n{body[:MAX_RESOLUTION_TEXT]}")
        deterministic = [
            candidate
            for candidate in candidates
            if any(
                len(normalize(value)) >= MIN_TOKEN_LENGTH and normalize(value) in normalized_mail
                for value in (candidate.name, candidate.external_code or "", candidate.alias or "")
            )
        ]
        if len(deterministic) == 1:
            return ResolutionResult(
                "AUTO_MATCH", deterministic[0].project_id, 99, tuple(candidates), "DETERMINISTIC"
            )
        if not candidates or provider is None:
            return ResolutionResult("MANUAL_REVIEW", None, None, tuple(candidates), "NONE")
        raw = await provider(
            {
                "schema_version": "PROJECT_RESOLUTION_V1",
                "subject": subject[:500],
                "summary": body[:MAX_RESOLUTION_TEXT],
                "candidate_options": [
                    {
                        "option_id": item.option_id,
                        "name": item.name,
                        "external_code": item.external_code,
                        "alias": item.alias,
                        "status": item.status,
                    }
                    for item in candidates
                ],
            }
        )
        decision, option_id, confidence = self.parse_provider_output(raw)
        selected = next((item for item in candidates if item.option_id == option_id), None)
        if decision == "MATCH" and selected is not None and confidence >= 85:
            return ResolutionResult(
                "AUTO_MATCH", selected.project_id, confidence, tuple(candidates), "AI"
            )
        return ResolutionResult("MANUAL_REVIEW", None, confidence, tuple(candidates), "AI")

    @staticmethod
    def parse_provider_output(raw: str) -> tuple[str, str | None, int]:
        import json

        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("PROJECT_RESOLUTION_INVALID_OUTPUT") from None
        if not isinstance(value, dict) or set(value) != {"decision", "option_id", "confidence"}:
            raise ValueError("PROJECT_RESOLUTION_INVALID_OUTPUT")
        decision, option_id, confidence = value.values()
        if decision not in {"MATCH", "AMBIGUOUS", "NO_MATCH"}:
            raise ValueError("PROJECT_RESOLUTION_INVALID_OUTPUT")
        if option_id is not None and not isinstance(option_id, str):
            raise ValueError("PROJECT_RESOLUTION_INVALID_OUTPUT")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int)
            or not 0 <= confidence <= 100
        ):
            raise ValueError("PROJECT_RESOLUTION_INVALID_OUTPUT")
        if decision == "MATCH" and not option_id:
            raise ValueError("PROJECT_RESOLUTION_INVALID_OUTPUT")
        if decision != "MATCH" and option_id is not None:
            raise ValueError("PROJECT_RESOLUTION_INVALID_OUTPUT")
        return decision, option_id, confidence


__all__ = [
    "MAX_PROJECT_RESOLUTION_CANDIDATES",
    "ProjectResolutionService",
    "ResolutionCandidate",
    "ResolutionResult",
    "normalize",
    "search_tokens",
]
