"""Mailbox-owned Provider V2 request boundaries.

Mailbox prompts and output contracts deliberately remain separate from Agent
Core.  This module only translates bounded mailbox payloads to the closed V2
runtime and returns provider content; it never exposes provider credentials or
wire details to mailbox workers.
"""

from __future__ import annotations

from typing import TypedDict

from risk_platform.ai_providers.v2_adapter import (
    ProviderChatRequest,
    ProviderError,
    ProviderErrorClassification,
    ProviderMessage,
    ProviderResponseFormat,
    ProviderRole,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime


class MailRiskExtractionItem(TypedDict):
    project_option_id: str
    category_option_id: str
    level: str
    description: str
    evidence: str
    suggestion: str
    confidence: int


class MailRiskExtractionOutput(TypedDict):
    risks: list[MailRiskExtractionItem]


_RISK_EXTRACTION_INSTRUCTION = """Extract actual risks from the supplied mailbox-derived content.
Return one JSON object only, with no markdown fence and no other top-level fields:
{"risks":[{"project_option_id":"P1","category_option_id":"C1","level":"HIGH","description":"...","evidence":"...","suggestion":"...","confidence":90}]}
The output schema is MailRiskExtractionOutput. Every risks item must contain exactly
these seven fields:
project_option_id, category_option_id, level, description, evidence, suggestion, confidence.
project_option_id must be exactly one option_id from project_options.
category_option_id must be exactly one option_id from risk_category_options.
Never return UUIDs, names, codes, free text categories, or invented IDs.
level must be exactly HIGH, MEDIUM, or LOW. description must describe the actual risk.
evidence may only cite facts present in the supplied mail-derived content and must not invent facts.
suggestion is a recommended measure,
not a claim that the measure already happened. confidence must be an integer from 0 through 100.
Normal progress is not a risk. Explicit delay, overdue work, blockage, or material impact
is a risk candidate.
If there is no risk, return exactly {"risks":[]}.
"""


class MailboxProviderV2:
    def __init__(self, runtime: ProviderV2Runtime) -> None:
        self._runtime = runtime

    async def resolve_project(self, payload: dict[str, object]) -> str:
        return await self._complete(
            "You resolve a mail to one server-provided project option. "
            "Return JSON only with exactly decision, option_id, confidence. "
            "decision is MATCH, AMBIGUOUS, or NO_MATCH; option_id must be one "
            "of the supplied options or null; confidence is an integer 0-100. "
            "Never invent IDs, projects, or options.",
            payload,
        )

    async def extract_risks(self, payload: dict[str, object]) -> str:
        return await self._complete(_RISK_EXTRACTION_INSTRUCTION, payload, json_mode=True)

    async def _complete(
        self, instruction: str, payload: dict[str, object], *, json_mode: bool = False
    ) -> str:
        response = await self._runtime.chat(
            ProviderChatRequest(
                messages=(
                    ProviderMessage(ProviderRole.SYSTEM, instruction),
                    ProviderMessage(ProviderRole.USER, _json_payload(payload)),
                ),
                response_format=ProviderResponseFormat.JSON_OBJECT if json_mode else None,
            )
        )
        if not response.content:
            raise ProviderError(
                classification=ProviderErrorClassification.MALFORMED_RESPONSE,
                retryable=False,
                failover_allowed=False,
            )
        return response.content


def _json_payload(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


__all__ = ["MailRiskExtractionItem", "MailRiskExtractionOutput", "MailboxProviderV2"]
