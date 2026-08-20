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


def validate_scope_for_role(role_code: str, data_scope: DataScopeType) -> None:
    allowed = _ALLOWED_SCOPES.get(role_code)
    if allowed is not None and data_scope not in allowed:
        raise ApiError(400, "BAD_REQUEST", "所选数据范围不符合该默认角色的权限边界")


__all__ = ["validate_scope_for_role"]
