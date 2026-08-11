from __future__ import annotations

import pytest
from pydantic import ValidationError

from risk_platform.system_config.schemas import PublishRequest
from risk_platform.system_config.service import SystemConfigService


def _payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "categories": [
            {
                "code": " DELIVERY ",
                "name": "交付风险",
                "keywords": ["延期"],
                "colorToken": "#4c8fe8",
                "description": None,
                "defaultLevel": "HIGH",
                "sortOrder": 0,
                "isActive": True,
            }
        ],
        "levels": [
            {"level": "HIGH", "displayName": "高", "colorToken": "#ff0000", "criteria": "影响交付", "keywords": ["延期"], "sortOrder": 0, "isActive": True},
            {"level": "MEDIUM", "displayName": "中", "colorToken": "#ffaa00", "criteria": "需要关注", "keywords": ["关注"], "sortOrder": 1, "isActive": True},
            {"level": "LOW", "displayName": "低", "colorToken": "#00aa00", "criteria": "一般事项", "keywords": ["一般"], "sortOrder": 2, "isActive": True},
        ],
        "aliases": [],
        "mail": {"syncIntervalMinutes": 30, "initialSyncDays": 90, "subjectKeywords": ["周报"], "riskKeywords": ["风险"]},
        "security": {"sessionHours": 8, "idleTimeoutMinutes": 30, "loginMaxAttempts": 5, "loginLockMinutes": 30, "passwordMinLength": 12},
        "notifications": {"mailboxSyncFailure": True, "apiKeyExpiry": True, "apiKeyExpiryDays": 30, "importFailure": True, "abnormalLogin": True},
        "changeCount": 1,
        "changeSummary": "更新风险规则",
        "module": "RISK",
    }
    payload.update(changes)
    return payload


def test_publish_contract_accepts_exact_three_levels() -> None:
    request = PublishRequest.model_validate(_payload())
    assert {item.level for item in request.levels} == {"HIGH", "MEDIUM", "LOW"}


def test_publish_contract_rejects_incomplete_levels() -> None:
    levels = _payload()["levels"]
    assert isinstance(levels, list)
    with pytest.raises(ValidationError):
        PublishRequest.model_validate(_payload(levels=levels[:2]))


def test_system_config_normalization_matches_alias_invariant() -> None:
    assert SystemConfigService._alias("  项目\u3000A ") == "项目a"
    assert SystemConfigService._code(" delivery-risk ") == "DELIVERY_RISK"
    with pytest.raises(Exception):
        SystemConfigService._code("bad code!")
