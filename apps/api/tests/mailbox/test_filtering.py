"""Deterministic MailCandidateFilter unit tests (no database, no IMAP)."""

from __future__ import annotations

from risk_platform.mailbox.filtering import (
    DEFAULT_WEEKLY_REPORT_KEYWORDS,
    MailCandidateFilter,
    MailCandidateFilterConfig,
    normalize_text,
)


def test_normalize_text_trims_casefolds_collapses_brackets_and_whitespace() -> None:
    assert normalize_text("  周报  ") == "周报"
    assert normalize_text("【周报】海外交付项目") == "周报海外交付项目"
    assert normalize_text("XX项目 周 报") == "xx项目 周 报"
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


def test_default_keywords_match_required_weekly_report_subjects() -> None:
    assert set(DEFAULT_WEEKLY_REPORT_KEYWORDS) >= {
        "周报",
        "项目周报",
        "工作周报",
        "项目工作周报",
    }


def test_default_filter_accepts_weekly_report_subjects_and_rejects_noise() -> None:
    flt = MailCandidateFilter()
    accepted = [
        "XX项目周报",
        "【周报】海外交付项目",
        "XX项目工作周报-2026W34",
        "工作周报",
        "项目工作周报",
    ]
    rejected = [
        "AD密码修改到期提醒",
        "验证码",
        "系统通知",
        None,
        "",
    ]
    for subject in accepted:
        assert flt.evaluate(subject=subject, sender="anyone@example.com") is True
    for subject in rejected:
        assert flt.evaluate(subject=subject, sender="anyone@example.com") is False


def test_weekly_report_only_false_accepts_everything() -> None:
    flt = MailCandidateFilter(MailCandidateFilterConfig(weekly_report_only=False))
    assert flt.evaluate(subject="AD密码修改到期提醒", sender="x@y.com") is True
    assert flt.evaluate(subject=None, sender=None) is True


def test_custom_subject_keywords_change_behavior() -> None:
    flt = MailCandidateFilter(MailCandidateFilterConfig(subject_keywords=("里程碑",)))
    assert flt.evaluate(subject="项目里程碑汇报", sender="x@y.com") is True
    assert flt.evaluate(subject="项目周报", sender="x@y.com") is False


def test_empty_subject_keywords_fall_back_to_server_default() -> None:
    flt = MailCandidateFilter(MailCandidateFilterConfig(subject_keywords=()))
    assert flt.evaluate(subject="项目周报", sender="x@y.com") is True
    assert flt.evaluate(subject="AD密码修改到期提醒", sender="x@y.com") is False


def test_sender_allowlist_restricts_when_non_empty() -> None:
    flt = MailCandidateFilter(
        MailCandidateFilterConfig(sender_allowlist=("@example.com", "reporter@"))
    )
    assert flt.evaluate(subject="项目周报", sender="reporter@example.com") is True
    assert flt.evaluate(subject="项目周报", sender="outsider@other.com") is False
    assert flt.evaluate(subject="项目周报", sender=None) is False


def test_sender_allowlist_empty_does_not_restrict() -> None:
    flt = MailCandidateFilter(MailCandidateFilterConfig(sender_allowlist=()))
    assert flt.evaluate(subject="项目周报", sender="outsider@other.com") is True


def test_filter_never_touches_body() -> None:
    # The filter is a pure function over subject/sender metadata; there is no
    # body parameter at all, so non-candidate mail can never have its body read.
    import inspect

    sig = inspect.signature(MailCandidateFilter.evaluate)
    assert set(sig.parameters) == {"self", "subject", "sender"}
