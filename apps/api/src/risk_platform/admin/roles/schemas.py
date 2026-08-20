"""HTTP contracts for role and permission administration."""

from __future__ import annotations

from pydantic import Field, field_validator

from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.http import StrictRequestModel


class PermissionResponse(StrictRequestModel):
    id: str
    code: str
    name: str
    module: str
    description: str | None


class RoleResponse(StrictRequestModel):
    id: str
    code: str
    name: str
    description: str | None
    isSystem: bool
    enabled: bool
    defaultDataScope: DataScopeType
    allowedDataScopes: list[DataScopeType]
    userCount: int
    permissionCodes: list[str]
    updatedAt: str


class CreateRoleRequest(StrictRequestModel):
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{2,63}$",
    )
    description: str | None = Field(default=None, max_length=500)
    enabled: bool
    defaultDataScope: DataScopeType
    permissionCodes: list[str]

    @field_validator("permissionCodes")
    @classmethod
    def unique_permissions(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("permissionCodes must be unique")
        return value


class UpdateRoleRequest(StrictRequestModel):
    name: str = Field(min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool
    defaultDataScope: DataScopeType
    permissionCodes: list[str]

    @field_validator("permissionCodes")
    @classmethod
    def unique_permissions(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("permissionCodes must be unique")
        return value


__all__ = [
    "CreateRoleRequest",
    "PermissionResponse",
    "RoleResponse",
    "UpdateRoleRequest",
]
