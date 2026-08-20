from __future__ import annotations

import pytest

from risk_platform.admin.users.policy import validate_scope_for_role
from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.errors import ApiError


def test_risk_admin_can_use_all_or_assigned_project_scope() -> None:
    validate_scope_for_role("RISK_ADMIN", DataScopeType.ALL)
    validate_scope_for_role("RISK_ADMIN", DataScopeType.ASSIGNED)


@pytest.mark.parametrize(
    "scope",
    [DataScopeType.OWNED, DataScopeType.OWNED_OR_ASSIGNED, DataScopeType.NONE],
)
def test_risk_admin_rejects_non_risk_project_scopes(scope: DataScopeType) -> None:
    with pytest.raises(ApiError):
        validate_scope_for_role("RISK_ADMIN", scope)
