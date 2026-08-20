"""Additive Admin HTTP contracts for AI Provider V2."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from risk_platform.shared.http import StrictRequestModel

ProviderTypeValue = Literal["DEEPSEEK_OFFICIAL"]
AccountHealthValue = Literal["UNTESTED", "AVAILABLE", "CREDENTIAL_ERROR"]
ModelHealthValue = Literal["UNTESTED", "AVAILABLE", "CONFIG_ERROR"]


class CreateProviderAccountRequest(StrictRequestModel):
    name: str = Field(min_length=2, max_length=128)
    providerType: ProviderTypeValue = "DEEPSEEK_OFFICIAL"
    apiKey: str = Field(min_length=8, max_length=500)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


class UpdateProviderAccountRequest(StrictRequestModel):
    name: str = Field(min_length=2, max_length=128)
    enabled: bool

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


class RotateProviderAccountKeyRequest(StrictRequestModel):
    apiKey: str = Field(min_length=8, max_length=500)


class ProviderAccountStatusRequest(StrictRequestModel):
    enabled: bool


class ProviderAccountResponse(StrictRequestModel):
    id: str
    name: str
    providerType: ProviderTypeValue
    maskedKey: str
    enabled: bool
    health: AccountHealthValue
    lastHealthAt: str | None
    lastHealthErrorCode: str | None
    modelCount: int
    createdAt: str
    updatedAt: str


class CreateModelConfigRequest(StrictRequestModel):
    modelName: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    isDefault: bool = False
    priority: int = Field(default=100, ge=0, le=1_000_000)
    timeoutSeconds: int = Field(default=60, ge=1, le=300)

    @field_validator("modelName")
    @classmethod
    def model_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("modelName must not be blank")
        return value.strip()


class UpdateModelConfigRequest(CreateModelConfigRequest):
    pass


class ModelConfigStatusRequest(StrictRequestModel):
    enabled: bool


class ModelConfigResponse(StrictRequestModel):
    id: str
    accountId: str
    modelName: str
    enabled: bool
    isDefault: bool
    priority: int
    timeoutSeconds: int
    health: ModelHealthValue
    lastHealthAt: str | None
    lastHealthErrorCode: str | None
    createdAt: str
    updatedAt: str


class DiscoveredModelResponse(StrictRequestModel):
    id: str


class ProviderV2ConnectionResult(StrictRequestModel):
    accountId: str
    modelConfigId: str | None
    success: bool
    latencyMs: int
    errorClassification: str | None
    availableModels: list[DiscoveredModelResponse]


__all__ = [
    "CreateModelConfigRequest",
    "CreateProviderAccountRequest",
    "DiscoveredModelResponse",
    "ModelConfigResponse",
    "ModelConfigStatusRequest",
    "ProviderAccountResponse",
    "ProviderAccountStatusRequest",
    "ProviderV2ConnectionResult",
    "RotateProviderAccountKeyRequest",
    "UpdateModelConfigRequest",
    "UpdateProviderAccountRequest",
]
