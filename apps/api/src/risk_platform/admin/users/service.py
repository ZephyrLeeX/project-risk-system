"""Transactional application service for administrator user management."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.admin.users.policy import (
    validate_owned_projects_for_role,
    validate_scope_for_role,
)
from risk_platform.admin.users.schemas import (
    AdminUserResponse,
    DepartmentResponse,
    PaginatedUsersResponse,
    ProjectScopesResponse,
    RoleResponse,
    UserAuditRecordResponse,
    UserMutationRequest,
    UserMutationResponse,
    UserSummaryResponse,
)
from risk_platform.audit.models import AuditActorType, AuditLog
from risk_platform.audit.service import AuditService
from risk_platform.auth.models import Session
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.projects.models import Project, ProjectStatus
from risk_platform.rbac.models import (
    DataScopeType,
    Permission,
    Role,
    RolePermission,
    UserProjectScope,
    UserRole,
)
from risk_platform.shared.errors import ApiError


class AdminUsersService:
    """Own user, assignment, session-revocation and audit transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._password_hasher = PasswordHasher(type=Type.ID)

    async def list_users(
        self,
        *,
        page: int,
        page_size: int,
        keyword: str | None,
        role_code: str | None,
        status: UserStatus | None,
        department_id: UUID | None,
    ) -> PaginatedUsersResponse:
        conditions = []
        if status is not None:
            conditions.append(User.status == status)
        if department_id is not None:
            conditions.append(User.departmentId == department_id)
        if role_code is not None:
            conditions.append(
                select(UserRole.userId)
                .join(Role, Role.id == UserRole.roleId)
                .where(UserRole.userId == User.id, Role.code == role_code)
                .exists()
            )
        if keyword is not None and (normalized := keyword.strip()):
            pattern = f"%{normalized}%"
            conditions.append(
                or_(
                    User.displayName.ilike(pattern),
                    User.username.ilike(pattern),
                    select(Department.id)
                    .where(Department.id == User.departmentId, Department.name.ilike(pattern))
                    .exists(),
                )
            )
        where = tuple(conditions)
        async with self._session_factory() as session:
            users = (
                await session.scalars(
                    select(User)
                    .where(*where)
                    .order_by(User.status.asc(), User.createdAt.asc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            total = await session.scalar(select(func.count()).select_from(User).where(*where))
            items = [await self._map_user(session, user) for user in users]
        return PaginatedUsersResponse(items=items, page=page, pageSize=page_size, total=total or 0)

    async def summary(self) -> UserSummaryResponse:
        async with self._session_factory() as session:
            totals = await session.execute(select(User.status, func.count()).group_by(User.status))
            counts = {status: count for status, count in totals.all()}
        return UserSummaryResponse(
            total=sum(counts.values()),
            active=counts.get(UserStatus.ACTIVE, 0),
            locked=counts.get(UserStatus.LOCKED, 0),
            disabled=counts.get(UserStatus.DISABLED, 0),
        )

    async def get(self, user_id: UUID) -> AdminUserResponse:
        async with self._session_factory() as session:
            return await self._map_user(session, await self._user_or_error(session, user_id))

    async def create(
        self, payload: UserMutationRequest, identity: SessionIdentity, trace_id: UUID
    ) -> UserMutationResponse:
        password = f"Risk!{secrets.token_urlsafe(12)}aA1"
        async with self._mutation_transaction(identity, trace_id, "ADMIN_USER_CREATED") as session:
            normalized = payload.username.strip().casefold()
            await self._ensure_username_available(session, normalized)
            role = await self._role_or_error(session, UUID(payload.roleId))
            data_scope = DataScopeType(payload.dataScope)
            validate_scope_for_role(role.code, data_scope)
            validate_owned_projects_for_role(role.code, payload.ownedProjectIds)
            project_ids = await self._validate_project_scopes(
                session, data_scope, payload.projectIds
            )
            await self._department_or_error(session, UUID(payload.departmentId))
            user = User(
                username=normalized,
                displayName=payload.displayName.strip(),
                email=_normalize_optional(payload.email),
                mobile=payload.mobile,
                passwordHash=await asyncio.to_thread(self._password_hasher.hash, password),
                departmentId=UUID(payload.departmentId),
                status=UserStatus.ACTIVE if payload.enabled else UserStatus.DISABLED,
                mustChangePassword=True,
            )
            session.add(user)
            await session.flush()
            session.add(UserRole(userId=user.id, roleId=role.id, dataScope=data_scope))
            await self._replace_project_scopes(
                session, user.id, project_ids, UUID(identity.user.id)
            )
            await self._replace_owned_projects(session, user.id, payload.ownedProjectIds)
            await self._audit(session, identity, trace_id, "ADMIN_USER_CREATED", user.id)
            response = await self._map_user(session, user)
        return UserMutationResponse(user=response, initialPassword=password)

    async def update(
        self, user_id: UUID, payload: UserMutationRequest, identity: SessionIdentity, trace_id: UUID
    ) -> UserMutationResponse:
        actor_id = UUID(identity.user.id)
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_USER_UPDATED", user_id
        ) as session:
            user = await self._user_or_error(session, user_id, for_update=True)
            current_role = await self._first_user_role(session, user_id)
            normalized = payload.username.strip().casefold()
            await self._ensure_username_available(session, normalized, user_id)
            role = await self._role_or_error(session, UUID(payload.roleId))
            data_scope = DataScopeType(payload.dataScope)
            validate_scope_for_role(role.code, data_scope)
            validate_owned_projects_for_role(role.code, payload.ownedProjectIds)
            project_ids = await self._validate_project_scopes(
                session, data_scope, payload.projectIds
            )
            await self._department_or_error(session, UUID(payload.departmentId))
            if user_id == actor_id and (
                current_role is None
                or current_role.roleId != role.id
                or current_role.dataScope != data_scope
                or not payload.enabled
            ):
                raise ApiError(
                    403, "FORBIDDEN", "不能修改当前登录账号自身的角色、数据范围或启用状态"
                )
            user.username = normalized
            user.displayName = payload.displayName.strip()
            user.email = _normalize_optional(payload.email)
            user.mobile = payload.mobile
            user.departmentId = UUID(payload.departmentId)
            user.status = UserStatus.ACTIVE if payload.enabled else UserStatus.DISABLED
            if not payload.enabled:
                user.failedLoginCount = 0
                user.lockedUntil = None
            await session.execute(delete(UserRole).where(UserRole.userId == user_id))
            session.add(UserRole(userId=user_id, roleId=role.id, dataScope=data_scope))
            await self._replace_project_scopes(session, user_id, project_ids, actor_id)
            await self._replace_owned_projects(session, user_id, payload.ownedProjectIds)
            if not payload.enabled:
                await self._revoke_sessions(session, user_id)
            await self._audit(session, identity, trace_id, "ADMIN_USER_UPDATED", user_id)
            response = await self._map_user(session, user)
        return UserMutationResponse(user=response)

    async def set_status(
        self, user_id: UUID, status: UserStatus, identity: SessionIdentity, trace_id: UUID
    ) -> AdminUserResponse:
        actor_id = UUID(identity.user.id)
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_USER_STATUS_UPDATED", user_id
        ) as session:
            if status not in {UserStatus.ACTIVE, UserStatus.DISABLED}:
                raise ApiError(400, "BAD_REQUEST", "账号状态仅支持启用或停用")
            if user_id == actor_id and status is UserStatus.DISABLED:
                raise ApiError(403, "FORBIDDEN", "不能停用当前登录账号")
            user = await self._user_or_error(session, user_id, for_update=True)
            user.status = status
            user.failedLoginCount = 0
            user.lockedUntil = None
            if status is UserStatus.DISABLED:
                await self._revoke_sessions(session, user_id)
            await self._audit(
                session,
                identity,
                trace_id,
                "ADMIN_USER_ENABLED" if status is UserStatus.ACTIVE else "ADMIN_USER_DISABLED",
                user_id,
            )
            return await self._map_user(session, user)

    async def unlock(
        self, user_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> AdminUserResponse:
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_USER_UNLOCKED", user_id
        ) as session:
            user = await self._user_or_error(session, user_id, for_update=True)
            user.status = UserStatus.ACTIVE
            user.failedLoginCount = 0
            user.lockedUntil = None
            await self._audit(session, identity, trace_id, "ADMIN_USER_UNLOCKED", user_id)
            return await self._map_user(session, user)

    async def reset_password(self, user_id: UUID, identity: SessionIdentity, trace_id: UUID) -> str:
        password = f"Risk!{secrets.token_urlsafe(12)}aA1"
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_USER_PASSWORD_RESET", user_id
        ) as session:
            user = await self._user_or_error(session, user_id, for_update=True)
            user.passwordHash = await asyncio.to_thread(self._password_hasher.hash, password)
            user.mustChangePassword = True
            user.passwordChangedAt = None
            user.failedLoginCount = 0
            user.lockedUntil = None
            if user.status is not UserStatus.DISABLED:
                user.status = UserStatus.ACTIVE
            await self._revoke_sessions(session, user_id)
            await self._audit(session, identity, trace_id, "ADMIN_USER_PASSWORD_RESET", user_id)
        return password

    async def get_project_scopes(self, user_id: UUID) -> ProjectScopesResponse:
        async with self._session_factory() as session:
            await self._user_or_error(session, user_id)
            role = await self._first_user_role(session, user_id)
            project_ids = await self._project_ids(session, user_id)
        return ProjectScopesResponse(
            dataScope=role.dataScope if role is not None else DataScopeType.NONE,
            projectIds=[str(project_id) for project_id in project_ids],
        )

    async def set_project_scopes(
        self,
        user_id: UUID,
        data_scope: DataScopeType,
        project_ids: list[str],
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> AdminUserResponse:
        actor_id = UUID(identity.user.id)
        async with self._mutation_transaction(
            identity, trace_id, "ADMIN_USER_SCOPE_UPDATED", user_id
        ) as session:
            user = await self._user_or_error(session, user_id, for_update=True)
            role = await self._first_user_role(session, user_id, for_update=True)
            if role is None:
                raise ApiError(409, "CONFLICT", "用户尚未分配角色")
            persisted_role = await self._role_or_error(session, role.roleId)
            validate_scope_for_role(persisted_role.code, data_scope)
            validated_project_ids = await self._validate_project_scopes(
                session, data_scope, project_ids
            )
            if user_id == actor_id and role.dataScope != data_scope:
                raise ApiError(403, "FORBIDDEN", "不能修改当前登录账号自身的数据范围")
            role.dataScope = data_scope
            await self._replace_project_scopes(session, user_id, validated_project_ids, actor_id)
            await self._audit(session, identity, trace_id, "ADMIN_USER_SCOPE_UPDATED", user_id)
            return await self._map_user(session, user)

    async def records(self, user_id: UUID) -> list[UserAuditRecordResponse]:
        async with self._session_factory() as session:
            await self._user_or_error(session, user_id)
            actor = User.__table__.alias("audit_actor")
            rows = (
                await session.execute(
                    select(AuditLog, actor.c.displayName)
                    .outerjoin(actor, actor.c.id == AuditLog.actorUserId)
                    .where(AuditLog.resourceType == "USER", AuditLog.resourceId == str(user_id))
                    .order_by(AuditLog.createdAt.desc())
                    .limit(100)
                )
            ).all()
        return [
            UserAuditRecordResponse(
                id=str(event.id),
                action=event.action,
                result=event.result.value,
                actorName=actor_name,
                createdAt=_iso(event.createdAt),
                summary=_audit_summary(event.action),
            )
            for event, actor_name in rows
        ]

    async def _user_or_error(
        self, session: AsyncSession, user_id: UUID, *, for_update: bool = False
    ) -> User:
        statement = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        user = await session.scalar(statement)
        if user is None:
            raise ApiError(404, "NOT_FOUND", "用户不存在")
        return user

    async def _role_or_error(self, session: AsyncSession, role_id: UUID) -> Role:
        role = await session.scalar(select(Role).where(Role.id == role_id))
        if role is None or not role.enabled:
            raise ApiError(400, "BAD_REQUEST", "所选角色不存在或已停用")
        return role

    async def _department_or_error(self, session: AsyncSession, department_id: UUID) -> None:
        if await session.get(Department, department_id) is None:
            raise ApiError(400, "BAD_REQUEST", "所选部门不存在")

    async def _ensure_username_available(
        self, session: AsyncSession, username: str, exclude_id: UUID | None = None
    ) -> None:
        statement = select(User.id).where(func.lower(User.username) == username)
        if exclude_id is not None:
            statement = statement.where(User.id != exclude_id)
        if await session.scalar(statement) is not None:
            raise ApiError(409, "CONFLICT", "登录账号已存在")

    async def _validate_project_scopes(
        self, session: AsyncSession, data_scope: DataScopeType, project_ids: list[str]
    ) -> list[UUID]:
        uses_assignments = data_scope in {DataScopeType.ASSIGNED, DataScopeType.OWNED_OR_ASSIGNED}
        if not uses_assignments and project_ids:
            raise ApiError(400, "BAD_REQUEST", "当前数据范围不使用指定项目授权")
        if data_scope is DataScopeType.ASSIGNED and not project_ids:
            raise ApiError(400, "BAD_REQUEST", "被授权项目范围至少选择一个项目")
        identifiers = [UUID(value) for value in project_ids]
        if identifiers:
            count = await session.scalar(
                select(func.count()).select_from(Project).where(Project.id.in_(identifiers))
            )
            if count != len(identifiers):
                raise ApiError(400, "BAD_REQUEST", "指定项目中包含不存在的项目")
        return identifiers

    async def _replace_project_scopes(
        self, session: AsyncSession, user_id: UUID, project_ids: list[UUID], actor_id: UUID
    ) -> None:
        await session.execute(delete(UserProjectScope).where(UserProjectScope.userId == user_id))
        session.add_all(
            [
                UserProjectScope(userId=user_id, projectId=project_id, assignedBy=actor_id)
                for project_id in project_ids
            ]
        )

    async def _validate_owned_projects(
        self, session: AsyncSession, user_id: UUID, owned_project_ids: list[UUID]
    ) -> list[Project]:
        """Lock and validate the target projects for ``managerId`` binding.

        Archived projects are never bindable, and a project already managed by a
        different account is never silently taken over (``PROJECT_MANAGER_CONFLICT``).
        """
        if not owned_project_ids:
            return []
        rows = list(
            await session.scalars(
                select(Project)
                .where(Project.id.in_(owned_project_ids))
                .with_for_update()
            )
        )
        if len(rows) != len(owned_project_ids):
            raise ApiError(400, "BAD_REQUEST", "指定项目中包含不存在的项目")
        for project in rows:
            if project.status is ProjectStatus.ARCHIVED:
                raise ApiError(400, "BAD_REQUEST", "归档项目不可绑定负责人")
            if project.managerId is not None and project.managerId != user_id:
                raise ApiError(
                    409,
                    "PROJECT_MANAGER_CONFLICT",
                    "所选项目已由其他负责人负责,不能静默接管",
                )
        return rows

    async def _replace_owned_projects(
        self, session: AsyncSession, user_id: UUID, owned_project_ids: list[str]
    ) -> None:
        """Bind ``Project.managerId`` to the confirmed target set.

        Projects previously owned by this user but no longer selected are unbound
        (``managerId`` back to NULL); the confirmed targets become owned by the
        user. The name-based recommendation never reaches this path directly --
        ownership only changes through an administrator-confirmed mutation.
        """
        target = await self._validate_owned_projects(
            session, user_id, [UUID(value) for value in owned_project_ids]
        )
        target_ids = {project.id for project in target}
        stale = (
            await session.scalars(
                select(Project).where(Project.managerId == user_id).with_for_update()
            )
        ).all()
        for project in stale:
            if project.id not in target_ids:
                project.managerId = None
        for project in target:
            project.managerId = user_id

    async def _revoke_sessions(self, session: AsyncSession, user_id: UUID) -> None:
        now = datetime.now(UTC)
        rows = await session.scalars(
            select(Session).where(Session.userId == user_id, Session.revokedAt.is_(None))
        )
        for row in rows:
            row.revokedAt = now

    async def _first_user_role(
        self, session: AsyncSession, user_id: UUID, *, for_update: bool = False
    ) -> UserRole | None:
        statement = (
            select(UserRole)
            .where(UserRole.userId == user_id)
            .order_by(UserRole.assignedAt.asc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(UserRole | None, await session.scalar(statement))

    async def _project_ids(self, session: AsyncSession, user_id: UUID) -> list[UUID]:
        return list(
            await session.scalars(
                select(UserProjectScope.projectId).where(UserProjectScope.userId == user_id)
            )
        )

    async def _owned_project_ids(self, session: AsyncSession, user_id: UUID) -> list[UUID]:
        """Projects whose ``managerId`` is this user, regardless of status.

        Bound-but-archived projects stay listed so editing preserves them instead
        of silently unbinding on the next save.
        """
        return list(
            await session.scalars(
                select(Project.id).where(Project.managerId == user_id)
            )
        )

    async def _map_user(self, session: AsyncSession, user: User) -> AdminUserResponse:
        department = await session.get(Department, user.departmentId) if user.departmentId else None
        user_role = await self._first_user_role(session, user.id)
        role = await session.get(Role, user_role.roleId) if user_role else None
        project_ids = await self._project_ids(session, user.id)
        owned_ids = await self._owned_project_ids(session, user.id)
        role_response = await self._map_role(session, role) if role is not None else None
        return AdminUserResponse(
            id=str(user.id),
            username=user.username,
            displayName=user.displayName,
            email=user.email,
            mobile=user.mobile,
            department=(
                DepartmentResponse(
                    id=str(department.id), code=department.code, name=department.name
                )
                if department
                else None
            ),
            status=user.status,
            role=role_response,
            dataScope=user_role.dataScope if user_role else DataScopeType.NONE,
            assignedProjectIds=[str(project_id) for project_id in project_ids],
            assignedProjectCount=len(project_ids),
            ownedProjectIds=[str(project_id) for project_id in owned_ids],
            ownedProjectCount=len(owned_ids),
            mustChangePassword=user.mustChangePassword,
            lastLoginAt=_optional_iso(user.lastLoginAt),
            lockedUntil=_optional_iso(user.lockedUntil),
            createdAt=_iso(user.createdAt),
            updatedAt=_iso(user.updatedAt),
        )

    async def _map_role(self, session: AsyncSession, role: Role) -> RoleResponse:
        permissions = list(
            await session.scalars(
                select(Permission.code)
                .join(RolePermission, RolePermission.permissionId == Permission.id)
                .where(RolePermission.roleId == role.id)
                .order_by(Permission.code)
            )
        )
        count = await session.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.roleId == role.id)
        )
        return RoleResponse(
            id=str(role.id),
            code=role.code,
            name=role.name,
            description=role.description,
            isSystem=role.isSystem,
            enabled=role.enabled,
            defaultDataScope=role.defaultDataScope,
            userCount=count or 0,
            permissionCodes=permissions,
            updatedAt=_iso(role.updatedAt),
        )

    async def _audit(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        user_id: UUID,
    ) -> None:
        await AuditService(session).record_success(
            actor_id=UUID(identity.user.id),
            actor_type=AuditActorType.USER,
            module="ADMIN_USER",
            action=action,
            resource_type="USER",
            resource_id=str(user_id),
            trace_id=trace_id,
        )

    @asynccontextmanager
    async def _mutation_transaction(
        self,
        identity: SessionIdentity,
        trace_id: UUID,
        action: str,
        user_id: UUID | None = None,
    ) -> AsyncIterator[AsyncSession]:
        """Roll back rejected writes, then preserve their typed failure audit separately."""

        try:
            async with transaction(self._session_factory) as session:
                yield session
        except ApiError as error:
            async with transaction(self._session_factory) as audit_session:
                await AuditService(audit_session).record_failure(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="ADMIN_USER",
                    action=action,
                    resource_type="USER",
                    resource_id=str(user_id) if user_id is not None else None,
                    trace_id=trace_id,
                    failure_code=error.code,
                )
            raise


def _normalize_optional(value: str | None) -> str | None:
    normalized = value.strip() if value else ""
    return normalized or None


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _optional_iso(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None


def _audit_summary(action: str) -> str:
    return {
        "ADMIN_USER_CREATED": "创建用户账号",
        "ADMIN_USER_UPDATED": "更新账号、角色或项目范围",
        "ADMIN_USER_ENABLED": "启用用户账号",
        "ADMIN_USER_DISABLED": "停用用户账号并撤销会话",
        "ADMIN_USER_UNLOCKED": "解除登录锁定",
        "ADMIN_USER_PASSWORD_RESET": "重置初始密码并撤销会话",
        "ADMIN_USER_SCOPE_UPDATED": "更新项目数据范围",
    }.get(action, action)


__all__ = ["AdminUsersService"]
