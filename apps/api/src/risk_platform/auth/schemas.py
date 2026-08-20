"""Public authentication request and response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from risk_platform.shared.http import StrictRequestModel

type RoleCode = Literal["SYSTEM_ADMIN", "RISK_ADMIN", "PROJECT_MANAGER", "VIEWER_AUDITOR"]
type AuthMethod = Literal["PASSWORD", "WECHAT"]
type DataScope = Literal["ALL", "OWNED", "ASSIGNED", "OWNED_OR_ASSIGNED", "NONE"]


class LoginRequest(StrictRequestModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)


class ChangePasswordRequest(StrictRequestModel):
    currentPassword: str = Field(min_length=1, max_length=255)
    newPassword: str = Field(min_length=1, max_length=255)
    confirmPassword: str = Field(min_length=1, max_length=255)


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    displayName: str
    departmentName: str | None
    roleCodes: list[RoleCode]
    permissions: list[str]
    dataScope: DataScope
    mustChangePassword: bool
    authMethod: AuthMethod = "PASSWORD"


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: AuthenticatedUser
    expiresAt: str


class ChangePasswordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reloginRequired: Literal[True] = True


__all__ = [
    "AuthMethod",
    "AuthenticatedUser",
    "ChangePasswordRequest",
    "ChangePasswordResponse",
    "DataScope",
    "LoginRequest",
    "RoleCode",
    "SessionResponse",
]
