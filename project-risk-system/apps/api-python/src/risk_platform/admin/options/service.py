"""Read-only administration option service."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department
from risk_platform.admin.options.schemas import DepartmentResponse


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


__all__ = ["AdminOptionsService"]
