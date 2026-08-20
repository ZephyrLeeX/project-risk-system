"""Compatibility adapter for the shared bounded project-resolution query."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.mailbox.models import MailProjectMatchType
from risk_platform.projects.resolution_service import ProjectResolutionService
from risk_platform.rbac.models import DataScopeType

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
        candidates = await ProjectResolutionService().retrieve_candidates(
            session, subject, text, UUID(int=0), DataScopeType.ALL
        )
        matches: list[ProjectMatch] = []
        for candidate in candidates:
            normalized = normalize(candidate.name)
            if len(normalized) >= MIN_CANDIDATE_LENGTH and normalized in haystack:
                matches.append(
                    ProjectMatch(
                        candidate.project_id,
                        candidate.name,
                        MailProjectMatchType.EXACT,
                        98,
                        candidate.name[:MAX_MATCH_TEXT],
                    )
                )
        return matches


__all__ = ["MailProjectMatcher", "ProjectMatch", "normalize"]
