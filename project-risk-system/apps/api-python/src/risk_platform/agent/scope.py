"""V2 scope boundary.  It deliberately has no data or tool dependency."""

from __future__ import annotations

from enum import StrEnum


class ScopeDecision(StrEnum):
    ALLOWED = "ALLOWED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


OUT_OF_SCOPE_MESSAGE = "我只能协助查询和分析当前系统中的项目、风险、待办、周报和看板数据。"


class ScopePolicy:
    """Classify whether a request belongs to the system-data domain.

    Mutation intent is deliberately not a scope decision.  In-scope write
    requests may reach the closed proposal-tool catalogue; the proposal,
    confirmation, permission and server-only commit boundaries own mutation
    safety.
    """

    _SYSTEM_TERMS = ("项目", "风险", "待办", "周报", "看板", "项目状态", "风险状态")
    _WEEKLY_ADVICE_INTENTS = (
        "给出本周处理建议",
        "本周处理建议",
        "根据本周风险给出建议",
        "本周重点风险和建议",
        "本周应该优先处理什么",
    )

    def decide(self, message: str) -> ScopeDecision:
        text = message.strip()
        if not text:
            return ScopeDecision.OUT_OF_SCOPE
        if any(term in text for term in self._SYSTEM_TERMS):
            return ScopeDecision.ALLOWED
        return (
            ScopeDecision.ALLOWED
            if any(intent in text for intent in self._WEEKLY_ADVICE_INTENTS)
            else ScopeDecision.OUT_OF_SCOPE
        )


__all__ = ["OUT_OF_SCOPE_MESSAGE", "ScopeDecision", "ScopePolicy"]
