from __future__ import annotations

import pytest

from risk_platform.admin.users.policy import (
    allowed_scopes_for_role,
    validate_owned_projects_for_role,
    validate_scope_for_role,
)
from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.errors import ApiError

_ALL_SCOPES = tuple(DataScopeType)

# The full role × scope matrix. This table is the contract the roles API
# mirrors onto RoleResponse.allowedDataScopes and the frontend consumes —
# changing it here must regenerate the contracts and update the roles test.
EXPECTED_MATRIX: dict[str, frozenset[DataScopeType]] = {
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


@pytest.mark.parametrize("role_code", sorted(EXPECTED_MATRIX))
def test_seeded_roles_allow_exactly_their_confirmed_scopes(role_code: str) -> None:
    expected = EXPECTED_MATRIX[role_code]
    assert allowed_scopes_for_role(role_code) == expected
    for scope in _ALL_SCOPES:
        if scope in expected:
            validate_scope_for_role(role_code, scope)
        else:
            with pytest.raises(ApiError):
                validate_scope_for_role(role_code, scope)


def test_custom_roles_have_no_fixed_boundary() -> None:
    assert allowed_scopes_for_role("SOME_CUSTOM_ROLE") is None
    for scope in _ALL_SCOPES:
        validate_scope_for_role("SOME_CUSTOM_ROLE", scope)


@pytest.mark.parametrize(
    "role_code", ["SYSTEM_ADMIN", "RISK_ADMIN", "VIEWER_AUDITOR"]
)
def test_only_project_managers_may_bind_owned_projects(role_code: str) -> None:
    with pytest.raises(ApiError):
        validate_owned_projects_for_role(role_code, ["<project-id>"])


@pytest.mark.parametrize("role_code", sorted(EXPECTED_MATRIX))
def test_empty_owned_projects_is_always_valid(role_code: str) -> None:
    validate_owned_projects_for_role(role_code, [])


def test_project_manager_may_bind_owned_projects() -> None:
    validate_owned_projects_for_role("PROJECT_MANAGER", ["<project-id>"])
