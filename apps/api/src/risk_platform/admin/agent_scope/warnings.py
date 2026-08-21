"""Deterministic broad-BLOCK warnings for admin scope rules.

Runtime rules are administrative overrides evaluated before the builtin
baseline, so a mistaken BLOCK rule can silently reject large amounts of
legitimate business traffic.  This module owns the single pure analysis
function that flags such rules; warnings are *advisory only* — they never
block a save and carry no runtime effect.
"""

from __future__ import annotations

from typing import Final

from risk_platform.admin.agent_scope.schemas import ScopeRuleWarning
from risk_platform.agent.scope import (
    BUILTIN_DOMAIN_TERMS,
    ScopeDecision,
    ScopeRuleMatchType,
    normalize_scope_text,
)

# Deterministic warning codes that admins (and the web UI) can branch on.
BROAD_BLOCK_RULE: Final = "BROAD_BLOCK_RULE"
SHORT_BLOCK_PATTERN: Final = "SHORT_BLOCK_PATTERN"

_BROAD_BLOCK_MESSAGE = "该 BLOCK 规则可能覆盖系统正常业务请求"
_SHORT_BLOCK_MESSAGE = "该 BLOCK 规则匹配范围过宽（过短的模式会命中大量正常输入）"


def analyze_scope_rule_warnings(
    decision: str | ScopeDecision,
    match_type: str | ScopeRuleMatchType | None,
    pattern: str,
) -> list[ScopeRuleWarning]:
    """Return warnings for one rule; empty when unremarkable.

    Pure and total: no I/O, no raises, identical output for identical input.
    Accepts both raw enum values and their string forms so saved rows
    (``row.decision.value``) and unsaved candidate payloads share one path.

    V1 detects:

    * ``BROAD_BLOCK_RULE`` — a BLOCK rule whose pattern overlaps any core
      business term (``BUILTIN_DOMAIN_TERMS``) in either direction: the
      pattern *contains* a term (``"项目风险汇报"`` contains ``项目``) or is
      *contained in* one (``"项"`` inside ``项目``).  Because runtime rules
      override the builtin ALLOW baseline, such a rule can block ordinary
      business questions.
    * ``SHORT_BLOCK_PATTERN`` — a BLOCK rule whose normalized pattern is a
      single character, which matches (PHRASE) or equals (EXACT) far too much.
    """

    if str(decision) != ScopeDecision.BLOCK.value:
        return []
    del match_type  # both V1 match types share the same overlap analysis
    normalized = normalize_scope_text(pattern)
    if not normalized:  # blank patterns are rejected elsewhere; never warn here
        return []
    warnings: list[ScopeRuleWarning] = []
    overlapping = [
        term
        for term in BUILTIN_DOMAIN_TERMS
        if term in normalized or normalized in term
    ]
    if overlapping:
        warnings.append(
            ScopeRuleWarning(
                code=BROAD_BLOCK_RULE,
                message=f"{_BROAD_BLOCK_MESSAGE}（与内置业务词重叠：{'、'.join(overlapping)}）",
            )
        )
    if len(normalized) <= 1:
        warnings.append(ScopeRuleWarning(code=SHORT_BLOCK_PATTERN, message=_SHORT_BLOCK_MESSAGE))
    return warnings


__all__ = [
    "BROAD_BLOCK_RULE",
    "SHORT_BLOCK_PATTERN",
    "analyze_scope_rule_warnings",
]
