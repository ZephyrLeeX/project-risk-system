"""V2 scope boundary.  It deliberately has no data or tool dependency."""

from __future__ import annotations

from enum import StrEnum


class ScopeDecision(StrEnum):
    ALLOWED = "ALLOWED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    # An anchored short reference/correction (e.g. "不是 A，我说的是 B") whose
    # domain-ness cannot be confirmed deterministically from the text alone.
    # Layer 1 refuses to *positively* admit it (it carries no domain keyword)
    # but also refuses to *hard-reject* it, because a bare project name like
    # "江湾" is a legitimate correction of a prior project/risk selection.  It
    # is deferred to the model-level AGENT_SCOPE_POLICY (layer 2), which
    # re-refuses anything genuinely out of scope ("不是，我说的是天气").  This
    # replaces the old chase-the-bad-verb blacklist: layer 1 no longer tries to
    # distinguish a bare project name from "天气/邮件/翻译" inside a correction.
    CONTEXTUAL_AMBIGUOUS = "CONTEXTUAL_AMBIGUOUS"


OUT_OF_SCOPE_MESSAGE = "我只能协助查询和分析当前系统中的项目、风险、待办、周报和看板数据。"


class ScopePolicy:
    """Classify whether a request belongs to the system-data domain.

    ``decide`` is the *context-free* first layer: a request that carries a
    positive domain signal (a system term or a weekly-advice intent) is
    ``ALLOWED``; everything else is ``OUT_OF_SCOPE``.  It never returns
    ``CONTEXTUAL_AMBIGUOUS`` — that state only arises in ``ReadOnlyAgentCore``
    when an otherwise out-of-scope message is an anchored short
    reference/correction (see ``context.is_contextual_shorthand``), in which
    case the request is deferred to the model-level ``AGENT_SCOPE_POLICY``
    (layer 2) rather than hard-rejected by an ever-expanding keyword gate.

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
