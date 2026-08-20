"""HTTP contracts for Agent layer-1 scope rule administration."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from risk_platform.shared.http import StrictRequestModel

ScopeRuleDecisionValue = Literal["ALLOW", "BLOCK"]
ScopeRuleMatchTypeValue = Literal["EXACT", "PHRASE"]
ScopeDecisionValue = Literal["ALLOW", "BLOCK", "DEFER"]
ScopeDecisionSourceValue = Literal["BUILTIN", "RUNTIME_RULE", "DEFAULT"]


class ScopeRuleResponse(StrictRequestModel):
    id: str
    name: str
    decision: ScopeRuleDecisionValue
    matchType: ScopeRuleMatchTypeValue
    pattern: str
    priority: int
    enabled: bool
    description: str | None
    version: int
    createdBy: str | None
    createdAt: str
    updatedAt: str


class CreateScopeRuleRequest(StrictRequestModel):
    # New rules default to disabled: verify with /test before enabling so a
    # mistaken live rule cannot immediately mis-block production traffic.
    name: str = Field(min_length=2, max_length=100)
    decision: ScopeRuleDecisionValue
    matchType: ScopeRuleMatchTypeValue
    pattern: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=0, ge=0, le=1000)
    enabled: bool = False
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name", "pattern")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class UpdateScopeRuleRequest(StrictRequestModel):
    # ``version`` is a required optimistic-lock token: a PATCH built from a
    # stale read must never silently clobber another admin's change.
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=100)
    decision: ScopeRuleDecisionValue | None = None
    matchType: ScopeRuleMatchTypeValue | None = None
    pattern: str | None = Field(default=None, min_length=1, max_length=200)
    priority: int | None = Field(default=None, ge=0, le=1000)
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name", "pattern")
    @classmethod
    def strip_nonempty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ScopeRuleTestRequest(StrictRequestModel):
    message: str = Field(min_length=1, max_length=500)

    @field_validator("message")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ScopeRuleTestMatch(StrictRequestModel):
    id: str
    name: str
    matchType: ScopeRuleMatchTypeValue
    decision: ScopeRuleDecisionValue
    priority: int


class ScopeRuleTestResponse(StrictRequestModel):
    decision: ScopeDecisionValue
    source: ScopeDecisionSourceValue
    matchedRule: ScopeRuleTestMatch | None


__all__ = [
    "CreateScopeRuleRequest",
    "ScopeRuleResponse",
    "ScopeRuleTestMatch",
    "ScopeRuleTestRequest",
    "ScopeRuleTestResponse",
    "UpdateScopeRuleRequest",
]
