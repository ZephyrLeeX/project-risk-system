"""Read-only administration option service."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department
from risk_platform.admin.options.schemas import DepartmentResponse, ProjectOptionResponse
from risk_platform.projects.models import Project, ProjectStatus


class AdminOptionsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_departments(self) -> list[DepartmentResponse]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Department)
                    .where(Department.enabled.is_(True))
                    .order_by(Department.sortOrder.asc(), Department.name.asc())
                )
            ).all()
            return [
                DepartmentResponse(id=str(row.id), code=row.code, name=row.name)
                for row in rows
            ]

    async def list_projects(self) -> list[ProjectOptionResponse]:
        """Return non-archived projects for the data-scope selector.

        Mirrors the legacy ``admin.scope.manage`` selector: exclude archived
        projects, order by name ascending, join the owning department name
        (nullable) and bound the result to the legacy ``take: 500`` ceiling.
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Project, Department.name)
                    .join(Department, Department.id == Project.departmentId, isouter=True)
                    .where(Project.status != ProjectStatus.ARCHIVED)
                    .order_by(Project.name.asc())
                    .limit(500)
                )
            ).all()
            return [
                ProjectOptionResponse(
                    id=str(project.id),
                    externalCode=project.externalCode,
                    name=project.name,
                    departmentName=department_name,
                )
                for project, department_name in rows
            ]


__all__ = ["AdminOptionsService"]
