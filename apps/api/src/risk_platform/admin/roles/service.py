"""Transactional role, permission and department administration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.roles.policy import validate_role_policy
from risk_platform.admin.roles.schemas import (
    CreateRoleRequest,
    PermissionResponse,
    RoleResponse,
    UpdateRoleRequest,
)
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.rbac.models import Permission, Role, RolePermission, UserRole
from risk_platform.shared.errors import ApiError


class AdminRolesService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_roles(self) -> list[RoleResponse]:
        async with self._session_factory() as session:
            roles = (
                await session.scalars(
                    select(Role).order_by(Role.isSystem.desc(), Role.createdAt.asc())
                )
            ).all()
            return [await self._map_role(session, role) for role in roles]

    async def list_permissions(self) -> list[PermissionResponse]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Permission).order_by(Permission.module.asc(), Permission.code.asc())
                )
            ).all()
            return [
                PermissionResponse(
                    id=str(row.id),
                    code=row.code,
                    name=row.name,
                    module=row.module,
                    description=row.description,
                )
                for row in rows
            ]

    async def create(
        self, payload: CreateRoleRequest, identity: SessionIdentity, trace_id: UUID
    ) -> RoleResponse:
        async with self._mutation_transaction(identity, trace_id, "ADMIN_ROLE_CREATED") as session:
            code = payload.code.strip().upper()
            if await session.scalar(select(Role.id).where(Role.code == code)) is not None:
                raise ApiError(409, "CONFLICT", "角色编码已存在")
            permissions = await self._permissions_or_error(session, payload.permissionCodes)
            validate_role_policy(code, payload.permissionCodes, payload.defaultDataScope)
            role = Role(
                code=code,
                name=payload.name.strip(),
                description=_normalize_optional(payload.description),
                isSystem=False,
                enabled=payload.enabled,
                defaultDataScope=payload.defaultDataScope,
            )
            session.add(role)
            await session.flush()
            session.add_all(
                [
                    RolePermission(roleId=role.id, permissionId=permission.id)
                    for permission in permissions
                ]
            )
            await session.flush()
            await self._audit(session, identity, trace_id, "ADMIN_ROLE_CREATED", role.id)
            return await self._map_role(session, role)

    async def update(
        self,
        role_id: UUID,
        payload: UpdateRoleRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> RoleResponse:
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_ROLE_UPDATED", role_id
        ) as session:
            role = await self._role_or_error(session, role_id, for_update=True)
            if role.isSystem and not payload.enabled:
                raise ApiError(400, "BAD_REQUEST", "系统预置角色不可停用")
            permissions = await self._permissions_or_error(session, payload.permissionCodes)
            validate_role_policy(role.code, payload.permissionCodes, payload.defaultDataScope)
            role.name = payload.name.strip()
            role.description = _normalize_optional(payload.description)
            role.enabled = payload.enabled
            role.defaultDataScope = payload.defaultDataScope
            await session.execute(delete(RolePermission).where(RolePermission.roleId == role.id))
            session.add_all(
                [
                    RolePermission(roleId=role.id, permissionId=permission.id)
                    for permission in permissions
                ]
            )
            await session.flush()
            await self._audit(session, identity, trace_id, "ADMIN_ROLE_UPDATED", role.id)
            return await self._map_role(session, role)

    async def remove(self, role_id: UUID, identity: SessionIdentity, trace_id: UUID) -> None:
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_ROLE_DELETED", role_id
        ) as session:
            role = await self._role_or_error(session, role_id, for_update=True)
            if role.isSystem:
                raise ApiError(400, "BAD_REQUEST", "系统预置角色不可删除")
            if await session.scalar(
                select(func.count()).select_from(UserRole).where(UserRole.roleId == role.id)
            ):
                raise ApiError(409, "CONFLICT", "角色仍有关联用户，请先迁移用户")
            await session.delete(role)
            await self._audit(session, identity, trace_id, "ADMIN_ROLE_DELETED", role.id)

    async def _role_or_error(
        self, session: AsyncSession, role_id: UUID, *, for_update: bool = False
    ) -> Role:
        statement = select(Role).where(Role.id == role_id)
        if for_update:
            statement = statement.with_for_update()
        role = await session.scalar(statement)
        if role is None:
            raise ApiError(404, "NOT_FOUND", "角色不存在")
        return role

    async def _permissions_or_error(
        self, session: AsyncSession, codes: list[str]
    ) -> list[Permission]:
        rows = list(
            await session.scalars(select(Permission).where(Permission.code.in_(codes)))
        )
        if len(rows) != len(codes):
            raise ApiError(400, "BAD_REQUEST", "权限列表中包含不存在的权限编码")
        by_code = {row.code: row for row in rows}
        return [by_code[code] for code in codes]

    async def _map_role(self, session: AsyncSession, role: Role) -> RoleResponse:
        permission_codes = list(
            await session.scalars(
                select(Permission.code)
                .join(RolePermission, RolePermission.permissionId == Permission.id)
                .where(RolePermission.roleId == role.id)
                .order_by(Permission.code.asc())
            )
        )
        user_count = await session.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.roleId == role.id)
        )
        updated_at = role.updatedAt
        if updated_at is None:
            raise RuntimeError("role updated timestamp was not populated")
        return RoleResponse(
            id=str(role.id),
            code=role.code,
            name=role.name,
            description=role.description,
            isSystem=role.isSystem,
            enabled=role.enabled,
            defaultDataScope=role.defaultDataScope,
            userCount=int(user_count or 0),
            permissionCodes=permission_codes,
            updatedAt=updated_at.astimezone(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        )

    async def _audit(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        role_id: UUID,
    ) -> None:
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="ADMIN_ROLE",
            action=action,
            resource_type="ROLE",
            resource_id=str(role_id),
            trace_id=trace_id,
        )

    @asynccontextmanager
    async def _mutation_transaction(
        self,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        role_id: UUID | None = None,
    ) -> AsyncIterator[AsyncSession]:
        try:
            async with transaction(self._session_factory) as session:
                yield session
        except ApiError as error:
            async with transaction(self._session_factory) as audit_session:
                await AuditService(audit_session).record_failure(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="ADMIN_ROLE",
                    action=action,
                    resource_type="ROLE",
                    resource_id=str(role_id) if role_id is not None else None,
                    trace_id=trace_id,
                    failure_code=error.code,
                )
            raise


def _normalize_optional(value: str | None) -> str | None:
    normalized = value.strip() if value else ""
    return normalized or None


__all__ = ["AdminRolesService"]
