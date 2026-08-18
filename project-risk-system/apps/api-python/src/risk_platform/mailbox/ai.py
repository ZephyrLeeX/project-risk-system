"""Mailbox-owned Provider V2 request boundaries.

Mailbox prompts and output contracts deliberately remain separate from Agent
Core.  This module only translates bounded mailbox payloads to the closed V2
runtime and returns provider content; it never exposes provider credentials or
wire details to mailbox workers.
"""

from __future__ import annotations

from risk_platform.ai_providers.v2_adapter import (
    ProviderChatRequest,
    ProviderError,
    ProviderErrorClassification,
    ProviderMessage,
    ProviderRole,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime


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
        return await self._complete(
            "You extract bounded risk candidates from a mailbox message. "
            "Return JSON only with exactly one risks array. Every project and "
            "category must use an option ID supplied in the request. Do not "
            "create taxonomy, projects, or facts not supported by the text.",
            payload,
        )

    async def _complete(self, instruction: str, payload: dict[str, object]) -> str:
        response = await self._runtime.chat(
            ProviderChatRequest(
                messages=(
                    ProviderMessage(ProviderRole.SYSTEM, instruction),
                    ProviderMessage(ProviderRole.USER, _json_payload(payload)),
                )
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


__all__ = ["MailboxProviderV2"]
