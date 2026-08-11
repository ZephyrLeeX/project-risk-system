"""Protected role and permission boundaries from the approved legacy policy."""

from __future__ import annotations

from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.errors import ApiError

SYSTEM_ADMIN_REQUIRED = frozenset(
    {
        "dashboard.view",
        "admin.user.manage",
        "admin.role.manage",
        "admin.scope.manage",
        "admin.ai.manage",
        "admin.import.manage",
        "admin.config.manage",
        "admin.audit.view",
    }
)
SYSTEM_ADMIN_ONLY = frozenset(
    {
        "admin.user.manage",
        "admin.role.manage",
        "admin.scope.manage",
        "admin.ai.manage",
        "admin.import.manage",
        "admin.config.manage",
    }
)
RISK_ADMIN_ONLY = frozenset({"mailbox.manage_self", "mailbox.sync_self"})

_ALLOWED_SCOPES: dict[str, frozenset[DataScopeType]] = {
    "SYSTEM_ADMIN": frozenset({DataScopeType.ALL}),
    "RISK_ADMIN": frozenset({DataScopeType.ALL}),
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


def validate_role_policy(
    role_code: str, permission_codes: list[str], data_scope: DataScopeType
) -> None:
    permissions = set(permission_codes)
    if role_code == "SYSTEM_ADMIN" and not permissions >= SYSTEM_ADMIN_REQUIRED:
        raise ApiError(400, "BAD_REQUEST", "系统管理员核心权限不可移除")
    if role_code != "SYSTEM_ADMIN" and permissions & SYSTEM_ADMIN_ONLY:
        raise ApiError(
            400,
            "BAD_REQUEST",
            "用户、角色、范围、API Key、导入和系统配置权限仅限系统管理员角色",
        )
    if role_code != "RISK_ADMIN" and permissions & RISK_ADMIN_ONLY:
        raise ApiError(400, "BAD_REQUEST", "个人邮箱配置与同步权限仅限风险管理员角色")
    if role_code == "RISK_ADMIN" and not permissions >= RISK_ADMIN_ONLY:
        raise ApiError(400, "BAD_REQUEST", "风险管理员必须保留个人邮箱配置与同步权限")
    allowed = _ALLOWED_SCOPES.get(role_code)
    if allowed is not None and data_scope not in allowed:
        raise ApiError(400, "BAD_REQUEST", "所选数据范围不符合该默认角色的权限边界")


__all__ = [
    "RISK_ADMIN_ONLY",
    "SYSTEM_ADMIN_ONLY",
    "SYSTEM_ADMIN_REQUIRED",
    "validate_role_policy",
]
