"""Unit tests for the three-state (ALLOW/BLOCK/DEFER) builtin scope baseline."""

from __future__ import annotations

import pytest

from risk_platform.agent.scope import (
    OUT_OF_SCOPE_MESSAGE,
    ScopeDecision,
    ScopeDecisionSource,
    ScopePolicy,
    normalize_scope_text,
)


def test_unknown_natural_language_defaults_to_defer() -> None:
    # The core fix: colloquial follow-ups without domain keywords must no
    # longer be killed by a keyword whitelist — they defer to layer 2.
    for message in ("大足这边怎么样", "那南岸呢", "还有吗", "为什么", "怎么处理", "帮我看看"):
        evaluation = ScopePolicy().evaluate(message)
        assert evaluation.decision is ScopeDecision.DEFER
        assert evaluation.source is ScopeDecisionSource.DEFAULT
        assert evaluation.match is None


def test_clear_business_messages_allow() -> None:
    evaluation = ScopePolicy().evaluate("当前有哪些高风险")
    assert evaluation.decision is ScopeDecision.ALLOW
    assert evaluation.source is ScopeDecisionSource.BUILTIN

    for message in (
        "项目周报邮件为什么没有识别",
        "天气原因导致项目延期有什么风险",
        "代码变更会不会影响项目交付",
        "帮我看看待办",
    ):
        assert ScopePolicy().decide(message) is ScopeDecision.ALLOW, message


def test_domain_term_preempts_non_business_pattern() -> None:
    # ALLOW runs before BLOCK: a domain-bearing message never reaches the
    # non-business baseline, even when it also mentions mail/weather/translation.
    for message in (
        "项目周报邮件为什么没有识别",  # 邮件
        "天气原因导致项目延期有什么风险",  # 天气
        "把这条风险的描述发邮件提醒我",  # 邮件 without 写…邮件 intent
        "翻译一下这条风险的描述",  # 翻译
    ):
        evaluation = ScopePolicy().evaluate(message)
        assert evaluation.decision is ScopeDecision.ALLOW, message
        assert evaluation.source is ScopeDecisionSource.BUILTIN


def test_clear_non_business_messages_block() -> None:
    for message in (
        "帮我翻译成英文",
        "把这段翻译成英文",
        "今天北京天气怎么样",
        "明天重庆天气预报",
        "帮我写一封邮件",
        "帮我写封邮件",
        "帮我写个 Python 程序",
        "写代码",
        "帮我写一篇作文",
        "给我讲量子力学",
        "讲个笑话",
        "来说个笑话吧",
    ):
        evaluation = ScopePolicy().evaluate(message)
        assert evaluation.decision is ScopeDecision.BLOCK, message
        assert evaluation.source is ScopeDecisionSource.BUILTIN
        assert evaluation.match is None


def test_builtin_mail_pattern_is_anchored_to_writing_intent() -> None:
    # The mail pattern requires 写…邮件; a message that merely mentions mail
    # without a writing intent and without a domain term still defers.
    evaluation = ScopePolicy().evaluate("邮件")
    assert evaluation.decision is ScopeDecision.DEFER


def test_empty_and_whitespace_messages_block() -> None:
    for message in ("", "   ", "\n\t "):
        evaluation = ScopePolicy().evaluate(message)
        assert evaluation.decision is ScopeDecision.BLOCK
        assert evaluation.source is ScopeDecisionSource.DEFAULT
        assert evaluation.match is None


def test_normalize_applies_nfkc_trim_and_collapse() -> None:
    assert normalize_scope_text("  你好   世界  ") == "你好 世界"
    # NFKC folds full-width latin and ideographic space.
    assert normalize_scope_text("／１２３") == "/123"
    assert normalize_scope_text("项目　风险") == "项目 风险"


def test_normalization_applies_to_matching() -> None:
    # Full-width and padded input still hits the builtin baseline.
    assert ScopePolicy().decide("  当前有哪些高风险  ") is ScopeDecision.ALLOW
    assert ScopePolicy().decide("帮我　翻译成英文") is ScopeDecision.BLOCK
    # Whitespace is collapsed, not removed: an inserted space inside a domain
    # term breaks the substring, so such text defers rather than pretending
    # to match.
    assert ScopePolicy().decide("项 目") is ScopeDecision.DEFER


def test_weekly_advice_intents_still_allow() -> None:
    for message in (
        "给出本周处理建议",
        "本周处理建议",
        "根据本周风险给出建议",
        "本周重点风险和建议",
        "本周应该优先处理什么",
    ):
        assert ScopePolicy().decide(message) is ScopeDecision.ALLOW, message


def test_weekly_non_business_question_blocks() -> None:
    # Regression guard: a weather question in weekly clothing must be blocked
    # (the old whitelist-era test expected OUT_OF_SCOPE for the same input).
    assert ScopePolicy().decide("本周天气怎么样") is ScopeDecision.BLOCK


def test_prepare_is_a_noop_on_the_base_policy() -> None:
    import asyncio

    assert asyncio.run(ScopePolicy().prepare()) is None


def test_out_of_scope_message_is_unchanged() -> None:
    assert OUT_OF_SCOPE_MESSAGE == (
        "我只能协助查询和分析当前系统中的项目、风险、待办、周报和看板数据。"
    )


@pytest.mark.parametrize(
    ("decision", "value"),
    [
        (ScopeDecision.ALLOW, "ALLOW"),
        (ScopeDecision.BLOCK, "BLOCK"),
        (ScopeDecision.DEFER, "DEFER"),
    ],
)
def test_decision_enum_values(decision: ScopeDecision, value: str) -> None:
    assert decision.value == value
