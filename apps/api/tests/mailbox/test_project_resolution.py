from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.projects.resolution_service import (
    ProjectResolutionService,
    ResolutionCandidate,
    normalize,
    search_tokens,
)


def test_resolution_text_is_normalized_and_tokenized() -> None:
    assert normalize("ERP 系统升级") == "erp系统升级"
    assert "erp" in search_tokens("[WSLDEMO][周报] ERP 系统升级", "本周 ERP 系统升级周报如下")
    assert "系统升级" in search_tokens(
        "[WSLDEMO][周报] ERP 系统升级", "本周 ERP 系统升级周报如下"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"decision":"MATCH","option_id":"P1","confidence":92}', ("MATCH", "P1", 92)),
        ('{"decision":"AMBIGUOUS","option_id":null,"confidence":60}', ("AMBIGUOUS", None, 60)),
        ('{"decision":"NO_MATCH","option_id":null,"confidence":0}', ("NO_MATCH", None, 0)),
    ],
)
def test_provider_contract_is_strict(raw: str, expected: tuple[str, str | None, int]) -> None:
    assert ProjectResolutionService.parse_provider_output(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        '{"decision":"AMBIGUOUS","option_id":"P1","confidence":60}',
        '{"decision":"MATCH","option_id":"P1","confidence":101}',
    ],
)
def test_provider_contract_rejects_ambiguous_selection(raw: str) -> None:
    with pytest.raises(ValueError, match="PROJECT_RESOLUTION_INVALID_OUTPUT"):
        ProjectResolutionService.parse_provider_output(raw)


def test_invented_option_is_downgraded_to_manual_review(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ProjectResolutionService()
    candidate = ResolutionCandidate(
        "P1", UUID(int=1), "WSLDEMO-ERP 系统升级", "WSLDEMO-ERP", None, "DELIVERY"
    )

    async def candidates(*args: object, **kwargs: object) -> tuple[ResolutionCandidate, ...]:
        del args, kwargs
        return (candidate,)

    async def provider(payload: dict[str, object]) -> str:
        options = cast(list[dict[str, object]], payload["candidate_options"])
        assert [item["option_id"] for item in options] == ["P1"]
        return '{"decision":"MATCH","option_id":"P999","confidence":99}'

    monkeypatch.setattr(service, "retrieve_candidates", candidates)
    result = asyncio.run(
        service.resolve(
            cast(AsyncSession, object()),
            "ERP",
            "正文",
            candidate.project_id,
            "ALL",
            provider,
        )
    )
    assert result.decision == "MANUAL_REVIEW"
    assert result.project_id is None


def test_candidate_limit_is_hard_capped() -> None:
    assert ProjectResolutionService().max_candidates == 20
    with pytest.raises(ValueError):
        ProjectResolutionService(21)
