"""V2 scope boundary.  It deliberately has no data or tool dependency."""

from __future__ import annotations

from enum import StrEnum


class ScopeDecision(StrEnum):
    ALLOWED = "ALLOWED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


OUT_OF_SCOPE_MESSAGE = "我只能协助查询和分析当前系统中的项目、风险、待办、周报和看板数据。"


class ScopePolicy:
    """Reject general-chat and mutation requests before a provider or tool is used."""

    _SYSTEM_TERMS = ("项目", "风险", "待办", "周报", "看板", "项目状态", "风险状态")
    _MUTATION_TERMS = ("上报", "创建", "新增", "修改", "调整", "解除", "删除", "确认", "完成")

    def decide(self, message: str) -> ScopeDecision:
        text = message.strip()
        if not text or any(term in text for term in self._MUTATION_TERMS):
            return ScopeDecision.OUT_OF_SCOPE
        return (
            ScopeDecision.ALLOWED
            if any(term in text for term in self._SYSTEM_TERMS)
            else ScopeDecision.OUT_OF_SCOPE
        )


__all__ = ["OUT_OF_SCOPE_MESSAGE", "ScopeDecision", "ScopePolicy"]
