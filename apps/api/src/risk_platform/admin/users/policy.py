"""Fixed seeded-role boundaries used when assigning users."""

from __future__ import annotations

from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.errors import ApiError

_ALLOWED_SCOPES: dict[str, frozenset[DataScopeType]] = {
    "SYSTEM_ADMIN": frozenset({DataScopeType.ALL}),
    "RISK_ADMIN": frozenset({DataScopeType.ALL, DataScopeType.ASSIGNED}),
    "PROJECT_MANAGER": frozenset(
        {
            DataScopeType.OWNED,
            DataScopeType.ASSIGNED,
            DataScopeType.OWNED_OR_ASSIGNED,
            DataScopeType.NONE,
        }
    ),
    "VIEWER_AUDITOR": frozenset({DataScopeType.ASSIGNED, DataScopeType.NONE}),
}


def allowed_scopes_for_role(role_code: str) -> frozenset[DataScopeType] | None:
    """The fixed scope boundary of a seeded system role, ``None`` for custom roles.

    This is the single source of truth for role-scope boundaries; the roles API
    exposes it to clients (``RoleResponse.allowedDataScopes``) so the frontend
    never maintains a second copy that can drift.
    """

    return _ALLOWED_SCOPES.get(role_code)


def validate_scope_for_role(role_code: str, data_scope: DataScopeType) -> None:
    allowed = _ALLOWED_SCOPES.get(role_code)
    if allowed is not None and data_scope not in allowed:
        raise ApiError(400, "BAD_REQUEST", "所选数据范围不符合该默认角色的权限边界")


def validate_owned_projects_for_role(role_code: str, owned_project_ids: list[str]) -> None:
    """Server-side guard: only PROJECT_MANAGER may own projects.

    The frontend clears ``ownedProjectIds`` for other roles, but the API must
    not rely on that — a direct caller could otherwise bind “负责项目” to a
    role whose scope can never consult ownership.
    """

    if owned_project_ids and role_code != "PROJECT_MANAGER":
        raise ApiError(400, "BAD_REQUEST", "仅项目经理角色可绑定负责项目")


__all__ = [
    "allowed_scopes_for_role",
    "validate_owned_projects_for_role",
    "validate_scope_for_role",
]
