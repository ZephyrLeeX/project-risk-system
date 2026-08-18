"""HTTP contracts for compatible administrator user APIs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from risk_platform.admin.models import UserStatus
from risk_platform.rbac.models import DataScopeType
from risk_platform.shared.http import StrictRequestModel

DataScope = DataScopeType


class DepartmentResponse(BaseModel):
    id: str
    code: str
    name: str


class RoleResponse(BaseModel):
    id: str
    code: str
    name: str
    description: str | None
    isSystem: bool
    enabled: bool
    defaultDataScope: DataScopeType
    userCount: int
    permissionCodes: list[str]
    updatedAt: str


class AdminUserResponse(BaseModel):
    id: str
    username: str
    displayName: str
    email: str | None
    mobile: str | None
    department: DepartmentResponse | None
    status: UserStatus
    role: RoleResponse | None
    dataScope: DataScopeType
    assignedProjectIds: list[str]
    assignedProjectCount: int
    mustChangePassword: bool
    lastLoginAt: str | None
    lockedUntil: str | None
    createdAt: str
    updatedAt: str


class UserSummaryResponse(BaseModel):
    total: int
    active: int
    locked: int
    disabled: int


class PaginatedUsersResponse(BaseModel):
    items: list[AdminUserResponse]
    page: int
    pageSize: int
    total: int


class UserMutationRequest(StrictRequestModel):
    displayName: str = Field(min_length=2, max_length=128)
    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z][a-zA-Z0-9._-]{2,63}$",
    )
    email: str | None = Field(default=None, max_length=255)
    mobile: str | None = Field(default=None, min_length=11, max_length=32)
    departmentId: str
    roleId: str
    dataScope: DataScopeType
    projectIds: list[str]
    enabled: bool

    @field_validator("departmentId", "roleId", mode="before")
    @classmethod
    def valid_uuid(cls, value: object) -> object:
        from uuid import UUID

        if not isinstance(value, str):
            raise ValueError("must be UUID")
        UUID(value)
        return value

    @field_validator("projectIds")
    @classmethod
    def unique_project_ids(cls, value: list[str]) -> list[str]:
        from uuid import UUID

        if len(set(value)) != len(value):
            raise ValueError("projectIds must be unique")
        for item in value:
            UUID(item)
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        if value is not None and value.strip() and ("@" not in value or value.startswith("@")):
            raise ValueError("invalid email")
        return value

    @field_validator("mobile")
    @classmethod
    def valid_mobile(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if (
            len(normalized) != 11
            or not normalized.isdecimal()
            or not normalized.startswith("1")
        ):
            raise ValueError("invalid mobile")
        return normalized


class UserMutationResponse(BaseModel):
    user: AdminUserResponse
    initialPassword: str | None = None


class SetUserStatusRequest(StrictRequestModel):
    status: Literal["ACTIVE", "DISABLED"]


class SetProjectScopesRequest(StrictRequestModel):
    dataScope: DataScopeType
    projectIds: list[str]

    @field_validator("projectIds")
    @classmethod
    def valid_unique_project_ids(cls, value: list[str]) -> list[str]:
        from uuid import UUID

        if len(set(value)) != len(value):
            raise ValueError("projectIds must be unique")
        for item in value:
            UUID(item)
        return value


class ProjectScopesResponse(BaseModel):
    dataScope: DataScope
    projectIds: list[str]


class UserAuditRecordResponse(BaseModel):
    id: str
    action: str
    result: Literal["SUCCESS", "FAILURE"]
    actorName: str | None
    createdAt: str
    summary: str


__all__ = [
    "AdminUserResponse",
    "PaginatedUsersResponse",
    "ProjectScopesResponse",
    "SetProjectScopesRequest",
    "SetUserStatusRequest",
    "UserAuditRecordResponse",
    "UserMutationRequest",
    "UserMutationResponse",
    "UserSummaryResponse",
]
