"""Scoped read boundary for project consumers, including Agent V2."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.auth.service import SessionIdentity
from risk_platform.projects.models import Project, ProjectAlias
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.shared.errors import ApiError


class ProjectSearchQuery(BaseModel):
    keyword: str | None = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class ProjectQueryItem(BaseModel):
    id: UUID
    name: str
    alias: str | None
    status: str


class ProjectSearchResult(BaseModel):
    items: list[ProjectQueryItem]
    page: int
    pageSize: int
    total: int


class ProjectsQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def search(
        self, identity: SessionIdentity, query: ProjectSearchQuery
    ) -> ProjectSearchResult:
        scope = project_scope_predicate(
            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
        )
        filters = [scope]
        if query.keyword:
            pattern = f"%{query.keyword}%"
            filters.append(
                or_(
                    Project.name.ilike(pattern),
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
        async with self._sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Project)
                        .where(*filters)
                        .order_by(Project.name, Project.id)
                        .offset((query.page - 1) * query.pageSize)
                        .limit(query.pageSize)
                    )
                ).all()
            )
            total = int(await session.scalar(select(func.count(Project.id)).where(*filters)) or 0)
        return ProjectSearchResult(
            items=[self._item(row) for row in rows],
            page=query.page,
            pageSize=query.pageSize,
            total=total,
        )

    async def detail(self, identity: SessionIdentity, project_id: UUID) -> ProjectQueryItem:
        scope = project_scope_predicate(
            UUID(identity.user.id), DataScopeType(identity.user.dataScope)
        )
        async with self._sessions() as session:
            row = await session.scalar(select(Project).where(Project.id == project_id, scope))
        if row is None:
            raise ApiError(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问")
        return self._item(row)

    @staticmethod
    def _item(project: Project) -> ProjectQueryItem:
        return ProjectQueryItem(
            id=project.id, name=project.name, alias=project.alias, status=project.status.value
        )


__all__ = ["ProjectQueryItem", "ProjectSearchQuery", "ProjectSearchResult", "ProjectsQueryService"]
