"""Unit tests for the deterministic broad-BLOCK warning analysis."""

from __future__ import annotations

from risk_platform.admin.agent_scope.warnings import analyze_scope_rule_warnings


def _codes(decision: str, pattern: str) -> list[str]:
    return [warning.code for warning in analyze_scope_rule_warnings(decision, "PHRASE", pattern)]


def test_block_rules_overlapping_domain_terms_warn() -> None:
    # Pattern equals or is contained in a core business term.
    assert _codes("BLOCK", "项目") == ["BROAD_BLOCK_RULE"]
    assert _codes("BLOCK", "风险") == ["BROAD_BLOCK_RULE"]
    assert _codes("BLOCK", "周报") == ["BROAD_BLOCK_RULE"]
    assert _codes("BLOCK", "项") == ["BROAD_BLOCK_RULE", "SHORT_BLOCK_PATTERN"]
    # Pattern contains a core business term: blocking it blocks business.
    assert _codes("BLOCK", "项目风险汇报") == ["BROAD_BLOCK_RULE"]


def test_narrow_block_rules_do_not_warn() -> None:
    assert _codes("BLOCK", "早上好") == []
    assert _codes("BLOCK", "帮我写周计划外的闲聊") == []  # 周计划 is not a domain term
    assert _codes("BLOCK", "翻译成英文") == []


def test_allow_rules_never_warn() -> None:
    # Even a one-character ALLOW pattern is not a breadth risk.
    assert _codes("ALLOW", "项") == []
    assert analyze_scope_rule_warnings("ALLOW", "EXACT", "项目") == []


def test_short_single_character_block_warns_without_term_overlap() -> None:
    assert _codes("BLOCK", "呀") == ["SHORT_BLOCK_PATTERN"]


def test_blank_patterns_return_no_warnings() -> None:
    assert analyze_scope_rule_warnings("BLOCK", "PHRASE", "   ") == []


def test_full_width_pattern_is_normalized_before_analysis() -> None:
    # Padding whitespace is folded away, so the term overlap is still found.
    assert _codes("BLOCK", "　项目 ") == ["BROAD_BLOCK_RULE"]
    # Traditional variants are distinct characters and are not domain terms.
    assert _codes("BLOCK", "項目") == []
