"""Deterministic project-name matching for sanitized mail text only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.mailbox.models import MailProjectMatchType
from risk_platform.projects.models import Project, ProjectAlias, ProjectStatus

MAX_MATCHES: Final = 20
MAX_MATCH_TEXT: Final = 500
MIN_CANDIDATE_LENGTH: Final = 4


def normalize(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value.casefold(), flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class ProjectMatch:
    project_id: UUID
    project_name: str
    match_type: MailProjectMatchType
    confidence: int
    matched_text: str


class MailProjectMatcher:
    async def match(self, session: AsyncSession, subject: str, text: str) -> list[ProjectMatch]:
        haystack = normalize(f"{subject}\n{text[:20_000]}")
        if not haystack:
            return []
        rows = (
            await session.execute(
                select(Project, ProjectAlias)
                .outerjoin(
                    ProjectAlias,
                    (ProjectAlias.projectId == Project.id) & ProjectAlias.isActive.is_(True),
                )
                .where(Project.status != ProjectStatus.ARCHIVED)
                .order_by(Project.name.asc(), ProjectAlias.alias.asc())
            )
        ).all()
        candidates: dict[UUID, list[tuple[str, MailProjectMatchType]]] = {}
        names: dict[UUID, str] = {}
        for project, alias in rows:
            names[project.id] = project.name
            candidates.setdefault(project.id, []).append((project.name, MailProjectMatchType.EXACT))
            if project.alias:
                candidates[project.id].append((project.alias, MailProjectMatchType.ALIAS))
            if alias is not None:
                candidates[project.id].append((alias.alias, MailProjectMatchType.ALIAS))
        matches: list[ProjectMatch] = []
        for project_id, values in candidates.items():
            for candidate, kind in sorted(values, key=lambda item: len(item[0]), reverse=True):
                normalized = normalize(candidate)
                if len(normalized) >= MIN_CANDIDATE_LENGTH and normalized in haystack:
                    matches.append(
                        ProjectMatch(
                            project_id,
                            names[project_id],
                            kind,
                            98 if kind is MailProjectMatchType.EXACT else 95,
                            candidate[:MAX_MATCH_TEXT],
                        )
                    )
                    break
        return matches[:MAX_MATCHES]


__all__ = ["MailProjectMatcher", "ProjectMatch", "normalize"]
