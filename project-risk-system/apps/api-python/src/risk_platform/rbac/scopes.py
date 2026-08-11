"""Reusable PostgreSQL predicates for the five approved project scopes."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import Select, exists, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.rbac.models import DataScopeType, UserProjectScope

ProjectIdentifier = UUID | str


def project_scope_predicate(
    user_id: ProjectIdentifier, data_scope: DataScopeType | str
) -> ColumnElement[bool]:
    """Return one composable predicate; department membership is never consulted."""

    active = Project.status != ProjectStatus.ARCHIVED
    owned = Project.managerId == user_id
    assigned = exists(
        select(UserProjectScope.projectId).where(
            UserProjectScope.projectId == Project.id,
            UserProjectScope.userId == user_id,
        )
    )
    try:
        scope = DataScopeType(data_scope)
    except ValueError:
        raise ValueError("unsupported project data scope") from None
    if scope is DataScopeType.ALL:
        return active
    if scope is DataScopeType.OWNED:
        return active & owned
    if scope is DataScopeType.ASSIGNED:
        return active & assigned
    if scope is DataScopeType.OWNED_OR_ASSIGNED:
        return active & or_(owned, assigned)
    return false()


def apply_project_scope(
    statement: Select[tuple[Project]],
    user_id: ProjectIdentifier,
    data_scope: DataScopeType | str,
) -> Select[tuple[Project]]:
    """Apply the same scope predicate to list, count and detail queries."""

    return statement.where(project_scope_predicate(user_id, data_scope))


async def get_scoped_project(
    session: AsyncSession,
    project_id: UUID,
    user_id: ProjectIdentifier,
    data_scope: DataScopeType | str,
) -> Project | None:
    """Return no row for archived or out-of-scope projects, preventing existence leaks."""

    statement = apply_project_scope(
        select(Project).where(Project.id == project_id), user_id, data_scope
    )
    return cast(Project | None, await session.scalar(statement))


async def scoped_project_exists(
    session: AsyncSession,
    project_id: UUID,
    user_id: ProjectIdentifier,
    data_scope: DataScopeType | str,
) -> bool:
    """Check existence through the same scope predicate without exposing a row."""

    statement = select(
        exists().where(
            Project.id == project_id,
            project_scope_predicate(user_id, data_scope),
        )
    )
    return bool(await session.scalar(statement))


__all__ = [
    "ProjectIdentifier",
    "apply_project_scope",
    "get_scoped_project",
    "project_scope_predicate",
    "scoped_project_exists",
]
