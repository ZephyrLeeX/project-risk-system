"""V2 scope boundary (three-state).  It deliberately has no data or tool dependency.

Layer 1 classifies a user message as one of three states:

* ``ALLOW``  — the text carries a clear business-domain signal; proceed.
* ``BLOCK``  — the text expresses a clearly non-business intent; reject with
  the fixed ``OUT_OF_SCOPE_MESSAGE`` without any provider call.
* ``DEFER``  — the text alone cannot decide (natural follow-ups, bare project
  names, colloquial shorthand); proceed and let the model-level
  ``AGENT_SCOPE_POLICY`` system rule (layer 2, see ``ReadOnlyAgentCore``)
  make the final call.  ``DEFER`` is the default for anything unrecognized:
  layer 1 must never hard-reject a message merely because it lacks a domain
  keyword.

Runtime (admin-managed) rules are evaluated before the builtin baseline by
``DynamicScopePolicy`` (see ``scope_rules.py``); this module only owns the
small, code-reviewed builtin baseline.  Authorization (RBAC, DataScope, tool
catalogue, proposal confirmation) is deliberately out of scope here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "OUT_OF_SCOPE_MESSAGE",
    "ScopeDecision",
    "ScopeDecisionSource",
    "ScopeEvaluation",
    "ScopeMatch",
    "ScopePolicy",
    "ScopeRuleMatchType",
    "normalize_scope_text",
]


class ScopeDecision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    DEFER = "DEFER"


class ScopeRuleMatchType(StrEnum):
    """Runtime rule match types.

    V1 supports EXACT and PHRASE only — both are plain string comparisons with
    no regex execution.  REGEX stays out until an engine with a real execution
    time bound (e.g. an RE2-family engine) is available; until then the enum
    deliberately has no REGEX value so no dormant pattern surface exists.
    """

    EXACT = "EXACT"
    PHRASE = "PHRASE"


class ScopeDecisionSource(StrEnum):
    BUILTIN = "BUILTIN"
    RUNTIME_RULE = "RUNTIME_RULE"
    DEFAULT = "DEFAULT"


OUT_OF_SCOPE_MESSAGE = "我只能协助查询和分析当前系统中的项目、风险、待办、周报和看板数据。"


def normalize_scope_text(message: str) -> str:
    """Normalize user text for scope matching.

    NFKC folds full-width/half-width and compatibility characters, then the
    text is trimmed and runs of whitespace collapse to a single space (spaces
    are *not* removed entirely).  Both runtime rule patterns and the builtin
    baseline match against this normalized form.
    """

    return " ".join(unicodedata.normalize("NFKC", message).strip().split())


@dataclass(frozen=True, slots=True)
class ScopeMatch:
    """Which rule produced a decision, for logging and audit."""

    rule_id: str | None
    rule_name: str | None
    match_type: ScopeRuleMatchType | None
    priority: int | None = None


@dataclass(frozen=True, slots=True)
class ScopeEvaluation:
    decision: ScopeDecision
    source: ScopeDecisionSource
    match: ScopeMatch | None


class ScopePolicy:
    """Classify whether a request belongs to the system-data domain.

    Evaluation order (``evaluate``):

    1. empty (after normalization) → ``BLOCK`` / ``DEFAULT``
    2. runtime rules — none in this base class; ``DynamicScopePolicy``
       overrides this step (``RUNTIME_RULE``)
    3. builtin business signal → ``ALLOW`` / ``BUILTIN``
    4. builtin non-business intent → ``BLOCK`` / ``BUILTIN``
    5. anything else → ``DEFER`` / ``DEFAULT``

    The builtin ALLOW baseline runs *before* the BLOCK baseline, so a message
    carrying any domain term ("天气原因导致项目延期有什么风险",
    "项目周报邮件为什么没有识别") can never be hard-rejected by a non-business
    pattern.  BLOCK patterns express clear non-business *intents* (translation,
    weather small talk, writing mail/code/essays, physics, jokes) — never bare
    topic keywords, because "天气"/"邮件"/"代码" alone are legitimate inside
    business sentences.  The baseline is deliberately tiny: new business or
    non-business vocabulary belongs in admin-managed runtime rules, not here.

    Mutation intent is deliberately not a scope decision.  In-scope write
    requests may reach the closed proposal-tool catalogue; the proposal,
    confirmation, permission and server-only commit boundaries own mutation
    safety.
    """

    _DOMAIN_TERMS = ("项目", "风险", "待办", "周报", "看板")
    _WEEKLY_ADVICE_INTENTS = (
        "给出本周处理建议",
        "本周处理建议",
        "根据本周风险给出建议",
        "本周重点风险和建议",
        "本周应该优先处理什么",
    )
    # Intent phrases, never bare topic keywords: every entry is only reached
    # when the message carries no domain term at all (ALLOW runs first).
    _NON_BUSINESS_PHRASES = (
        "翻译",
        "天气怎么样",
        "天气预报",
        "今天天气",
        "明天天气",
        "量子力学",
        "相对论",
    )
    # Code-reviewed, module-level compiled patterns (known-linear, bounded).
    _NON_BUSINESS_PATTERNS = (
        re.compile(r"写.{0,3}邮件"),  # 写邮件/写封邮件/写一封邮件/帮我写个邮件
        re.compile(r"写.{0,10}(?:程序|代码|作文)"),  # 写程序/帮我写个 Python 程序/写作文
        re.compile(r"(?:讲|说|来)个?笑话"),
    )

    async def prepare(self) -> None:
        """Refresh runtime state before a decision (no-op in the base class).

        ``DynamicScopePolicy`` overrides this to TTL-gate a rule reload; the
        agent core awaits it once per execution so worker processes pick up
        admin rule changes without a restart.
        """

        return None

    def evaluate(self, message: str) -> ScopeEvaluation:
        text = normalize_scope_text(message)
        if not text:
            # An empty message expresses no business intent at all.
            return ScopeEvaluation(ScopeDecision.BLOCK, ScopeDecisionSource.DEFAULT, None)
        return self.evaluate_normalized(text)

    def evaluate_normalized(self, text: str) -> ScopeEvaluation:
        """Evaluate an already-normalized message against the builtin baseline.

        Public so ``DynamicScopePolicy`` can delegate its unmatched-text tail
        (steps 3-5) to this same builtin baseline.
        """

        if any(term in text for term in self._DOMAIN_TERMS) or any(
            intent in text for intent in self._WEEKLY_ADVICE_INTENTS
        ):
            return ScopeEvaluation(ScopeDecision.ALLOW, ScopeDecisionSource.BUILTIN, None)
        if any(phrase in text for phrase in self._NON_BUSINESS_PHRASES) or any(
            pattern.search(text) for pattern in self._NON_BUSINESS_PATTERNS
        ):
            return ScopeEvaluation(ScopeDecision.BLOCK, ScopeDecisionSource.BUILTIN, None)
        return ScopeEvaluation(ScopeDecision.DEFER, ScopeDecisionSource.DEFAULT, None)

    def decide(self, message: str) -> ScopeDecision:
        return self.evaluate(message).decision
