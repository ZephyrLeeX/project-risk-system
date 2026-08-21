"""HTTP contracts for Agent layer-1 scope rule administration."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from risk_platform.shared.http import StrictRequestModel

ScopeRuleDecisionValue = Literal["ALLOW", "BLOCK"]
ScopeRuleMatchTypeValue = Literal["EXACT", "PHRASE"]
ScopeDecisionValue = Literal["ALLOW", "BLOCK", "DEFER"]
ScopeDecisionSourceValue = Literal["BUILTIN", "RUNTIME_RULE", "DEFAULT"]
ScopeRuleWarningCodeValue = Literal["BROAD_BLOCK_RULE", "SHORT_BLOCK_PATTERN"]

# Label used as the matchedRule name when an unsaved candidate wins a preview,
# so the admin can tell it apart from a persisted rule (its id is "").
PREVIEW_CANDIDATE_LABEL = "(预览规则)"


class ScopeRuleWarning(StrictRequestModel):
    """Advisory notice that a rule may override normal business traffic.

    Warnings never block a save; they exist because runtime rules are
    administrative overrides evaluated before the builtin ALLOW baseline.
    """

    code: ScopeRuleWarningCodeValue
    message: str


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
    warnings: list[ScopeRuleWarning] = Field(default_factory=list)


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


class ScopeRuleCandidateRule(StrictRequestModel):
    """An unsaved rule to preview with /test before writing anything to PG.

    Identity fields (id, name, enabled, version) are deliberately absent:
    a candidate is ephemeral and produces no runtime side effects.
    """

    decision: ScopeRuleDecisionValue
    matchType: ScopeRuleMatchTypeValue
    pattern: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=0, ge=0, le=1000)

    @field_validator("pattern")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ScopeRuleTestRequest(StrictRequestModel):
    """Evaluate a message against the live policy, optionally plus one candidate.

    ``ruleId`` (a saved rule — may be disabled) and ``candidateRule`` (an
    unsaved draft) are mutually exclusive.  Without either, the message is
    evaluated against the current live policy only.
    """

    message: str = Field(min_length=1, max_length=500)
    ruleId: UUID | None = None
    candidateRule: ScopeRuleCandidateRule | None = None

    @field_validator("message")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def exclusive_preview_target(self) -> ScopeRuleTestRequest:
        if self.ruleId is not None and self.candidateRule is not None:
            raise ValueError("ruleId and candidateRule are mutually exclusive")
        return self


class ScopeRuleTestMatch(StrictRequestModel):
    id: str
    name: str
    matchType: ScopeRuleMatchTypeValue
    decision: ScopeRuleDecisionValue
    priority: int


class ScopeRuleTestResponse(StrictRequestModel):
    """Layer-1 result of a /test evaluation.

    ``preview`` is true when the evaluation included a rule that is *not*
    live (a disabled saved rule or an unsaved candidate).  A previewed
    ``RUNTIME_RULE`` match on a candidate has ``matchedRule.id == ""`` and
    ``matchedRule.name == "(预览规则)"``; it must not be mistaken for an
    already-effective rule.
    """

    decision: ScopeDecisionValue
    source: ScopeDecisionSourceValue
    matchedRule: ScopeRuleTestMatch | None
    preview: bool = False
    previewRuleId: str | None = None
    warnings: list[ScopeRuleWarning] = Field(default_factory=list)


__all__ = [
    "PREVIEW_CANDIDATE_LABEL",
    "CreateScopeRuleRequest",
    "ScopeRuleCandidateRule",
    "ScopeRuleResponse",
    "ScopeRuleTestMatch",
    "ScopeRuleTestRequest",
    "ScopeRuleTestResponse",
    "ScopeRuleWarning",
    "UpdateScopeRuleRequest",
]
