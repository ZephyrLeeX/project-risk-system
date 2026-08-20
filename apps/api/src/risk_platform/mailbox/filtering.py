"""Deterministic weekly-report candidate filter for IMAP envelope discovery.

The filter runs at the IMAP *discover header* stage, before any message body is
fetched, so that non-weekly-report mail (account alerts, verification codes,
system notifications) never enters ``mail_messages``, project matching or AI
risk extraction. It only consults lightweight envelope metadata
(``subject``/``sender``) — never the body — and never persists non-candidate
content.

The matching is intentionally substring-based after a conservative
normalization (trim, casefold, collapse whitespace, strip common brackets), so
``"XX项目周报"``, ``"【周报】海外交付项目"`` and ``"XX项目工作周报-2026W34"``
all hit the default keyword set, while ``"AD密码修改到期提醒"``,
``"验证码"`` and ``"系统通知"`` do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Server-provided first-version default weekly-report subject keywords.
#: Used both as the unconfigured-mailbox fallback and to seed the empty
#: overview form, so the default rule set is not scattered as hardcoded
#: literals across the module surface.
DEFAULT_WEEKLY_REPORT_KEYWORDS: Final[tuple[str, ...]] = (
    "周报",
    "项目周报",
    "工作周报",
    "项目工作周报",
)

#: Common CJK/Latin bracket characters stripped during subject normalization so
#: ``【周报】海外交付项目`` collapses to ``周报海外交付项目`` and the bare
#: keyword ``周报`` still matches as a substring.
_BRACKETS: Final[frozenset[str]] = frozenset("【】[]()（）「」『』〈〉《》〔〕｢｣")


def normalize_text(value: str | None) -> str:
    """Return a casefolded, bracket-stripped, whitespace-collapsed form."""

    if not value:
        return ""
    text = value.strip().casefold()
    text = "".join(char for char in text if char not in _BRACKETS)
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class MailCandidateFilterConfig:
    """Per-mailbox candidate-filter knobs (see ADR mailbox filter config)."""

    weekly_report_only: bool = True
    subject_keywords: tuple[str, ...] = DEFAULT_WEEKLY_REPORT_KEYWORDS
    sender_allowlist: tuple[str, ...] = ()

    @property
    def effective_keywords(self) -> tuple[str, ...]:
        """Configured keywords, falling back to the server default when empty."""

        return self.subject_keywords or DEFAULT_WEEKLY_REPORT_KEYWORDS


class MailCandidateFilter:
    """Deterministic envelope-level weekly-report candidate decision."""

    __slots__ = ("_config",)

    def __init__(self, config: MailCandidateFilterConfig | None = None) -> None:
        self._config = config or MailCandidateFilterConfig()

    def evaluate(self, *, subject: str | None, sender: str | None) -> bool:
        """Return ``True`` when the envelope is a weekly-report candidate."""

        if not self._config.weekly_report_only:
            return True
        if not self._subject_matches(subject):
            return False
        allowlist = self._normalized_allowlist()
        if allowlist and not self._sender_matches(sender, allowlist):
            return False
        return True

    def _subject_matches(self, subject: str | None) -> bool:
        haystack = normalize_text(subject)
        if not haystack:
            return False
        return any(
            bool(keyword) and keyword in haystack
            for keyword in (normalize_text(item) for item in self._config.effective_keywords)
        )

    def _sender_matches(self, sender: str | None, allowlist: tuple[str, ...]) -> bool:
        haystack = normalize_text(sender)
        if not haystack:
            return False
        return any(entry in haystack for entry in allowlist)

    def _normalized_allowlist(self) -> tuple[str, ...]:
        return tuple(
            entry for entry in (normalize_text(item) for item in self._config.sender_allowlist)
            if entry
        )


__all__ = [
    "DEFAULT_WEEKLY_REPORT_KEYWORDS",
    "MailCandidateFilter",
    "MailCandidateFilterConfig",
    "normalize_text",
]
