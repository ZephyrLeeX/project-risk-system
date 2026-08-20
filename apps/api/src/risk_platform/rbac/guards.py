"""FastAPI permission dependencies with no implicit data-scope behavior."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated

from fastapi import Depends

from risk_platform.auth.api import current_identity
from risk_platform.auth.service import SessionIdentity
from risk_platform.shared.errors import ApiError


def has_all_permissions(granted: Sequence[str], required: Sequence[str]) -> bool:
    """Require every declared permission; data scope is checked separately."""

    return all(permission in granted for permission in required)


def require_permissions(*permissions: str) -> Callable[..., object]:
    """Build a dependency that returns identity or raises a safe 403 error."""

    required = tuple(dict.fromkeys(permissions))

    async def guard(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> SessionIdentity:
        if not has_all_permissions(identity.user.permissions, required):
            raise ApiError(403, "FORBIDDEN", "当前账号无权执行此操作")
        return identity

    return guard


__all__ = ["has_all_permissions", "require_permissions"]
