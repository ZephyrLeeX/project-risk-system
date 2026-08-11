"""Authentication persistence boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.admin.models import Department, User
from risk_platform.auth.models import Session
from risk_platform.model_types import JSONValue
from risk_platform.rbac.models import Permission, Role, RolePermission, UserRole
from risk_platform.system_config.models import SystemConfigRelease


@dataclass(frozen=True, slots=True)
class UserAccess:
    department_name: str | None
    roles: tuple[tuple[str, str], ...]
    permissions: tuple[str, ...]


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_by_username(self, username: str, *, for_update: bool) -> User | None:
        statement: Select[tuple[User]] = select(User).where(
            func.lower(User.username) == username.casefold()
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(User | None, await self._session.scalar(statement))

    async def user_by_id(self, user_id: UUID, *, for_update: bool) -> User | None:
        statement: Select[tuple[User]] = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(User | None, await self._session.scalar(statement))

    async def user_access(self, user_id: UUID) -> UserAccess:
        department_name = await self._session.scalar(
            select(Department.name)
            .join(User, User.departmentId == Department.id)
            .where(User.id == user_id)
        )
        role_rows = (
            await self._session.execute(
                select(Role.code, UserRole.dataScope)
                .join(UserRole, UserRole.roleId == Role.id)
                .where(UserRole.userId == user_id, Role.enabled.is_(True))
                .order_by(Role.code)
            )
        ).all()
        permissions = (
            await self._session.scalars(
                select(Permission.code)
                .join(RolePermission, RolePermission.permissionId == Permission.id)
                .join(Role, Role.id == RolePermission.roleId)
                .join(UserRole, UserRole.roleId == Role.id)
                .where(UserRole.userId == user_id, Role.enabled.is_(True))
                .distinct()
                .order_by(Permission.code)
            )
        ).all()
        return UserAccess(
            department_name=department_name,
            roles=tuple((code, scope.value) for code, scope in role_rows),
            permissions=tuple(permissions),
        )

    async def create_session(
        self,
        *,
        token_hash: str,
        user_id: UUID,
        expires_at: datetime,
        client_ip_hash: str | None,
        user_agent: str | None,
    ) -> Session:
        row = Session(
            tokenHash=token_hash,
            userId=user_id,
            expiresAt=expires_at,
            clientIpHash=client_ip_hash,
            userAgent=user_agent,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def session_by_hash(self, token_hash: str, *, for_update: bool) -> Session | None:
        statement: Select[tuple[Session]] = select(Session).where(Session.tokenHash == token_hash)
        if for_update:
            statement = statement.with_for_update()
        return cast(Session | None, await self._session.scalar(statement))

    async def session_by_id(self, session_id: UUID, *, for_update: bool) -> Session | None:
        statement: Select[tuple[Session]] = select(Session).where(Session.id == session_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(Session | None, await self._session.scalar(statement))

    async def revoke_user_sessions(self, user_id: UUID, revoked_at: datetime) -> None:
        rows = await self._session.scalars(
            select(Session).where(Session.userId == user_id, Session.revokedAt.is_(None))
        )
        for row in rows:
            row.revokedAt = revoked_at

    async def latest_security_settings(self) -> Mapping[str, JSONValue]:
        snapshot = await self._session.scalar(
            select(SystemConfigRelease.snapshot)
            .order_by(SystemConfigRelease.publishedAt.desc())
            .limit(1)
        )
        if not isinstance(snapshot, dict):
            return {}
        security = snapshot.get("security")
        if not isinstance(security, dict):
            return {}
        return cast(Mapping[str, JSONValue], security)


__all__ = ["AuthRepository", "UserAccess"]
