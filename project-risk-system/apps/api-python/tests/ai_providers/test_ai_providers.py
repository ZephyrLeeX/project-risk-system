from __future__ import annotations

import pytest
from pydantic import ValidationError

from risk_platform.ai_providers.client import AiProviderClient, ProviderRequestError
from risk_platform.ai_providers.models import AiProviderProtocol
from risk_platform.ai_providers.schemas import CreateProviderRequest, DraftTestRequest
from risk_platform.shared.crypto import KeyRing, SecretCipher


def test_provider_contract_rejects_plain_http_and_short_key() -> None:
    with pytest.raises(ValidationError):
        CreateProviderRequest(
            name="Demo",
            vendor="Demo",
            endpoint="http://example.com",
            model="m",
            apiKey="short",
            timeoutSeconds=60,
            retryCount=2,
            enabled=True,
        )


def test_draft_contract_has_bounded_retry_and_timeout() -> None:
    with pytest.raises(ValidationError):
        DraftTestRequest(
            name="Demo",
            endpoint="https://example.com",
            model="m",
            apiKey="long-enough-key",
            timeoutSeconds=301,
            retryCount=2,
        )


def test_secret_cipher_envelope_does_not_expose_plaintext() -> None:
    cipher = SecretCipher(KeyRing(active_version="v1", keys={"v1": b"0" * 32}))
    encrypted = cipher.encrypt("super-secret-api-key")
    assert "super-secret-api-key" not in encrypted.envelope
    assert cipher.decrypt(encrypted.envelope) == "super-secret-api-key"


@pytest.mark.parametrize(
    ("protocol", "operation", "header"),
    [
        (AiProviderProtocol.OPENAI_CHAT_COMPLETIONS, "chat/completions", "Authorization"),
        (AiProviderProtocol.OPENAI_RESPONSES, "responses", "Authorization"),
        (AiProviderProtocol.ANTHROPIC_MESSAGES, "messages", "x-api-key"),
    ],
)
def test_protocol_adapters_use_explicit_operation_and_auth(
    protocol: AiProviderProtocol, operation: str, header: str
) -> None:
    assert AiProviderClient._operation(protocol) == operation
    headers = AiProviderClient._headers(protocol, "secret")
    assert headers[header]
    if protocol is AiProviderProtocol.ANTHROPIC_MESSAGES:
        assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.parametrize(
    ("protocol", "body", "expected"),
    [
        (
            AiProviderProtocol.OPENAI_CHAT_COMPLETIONS,
            '{"choices":[{"message":{"content":"{}"}}],"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}',
            {"input": 2, "output": 3, "total": 5},
        ),
        (
            AiProviderProtocol.OPENAI_RESPONSES,
            '{"output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}],"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}',
            {"input": 2, "output": 3, "total": 5},
        ),
        (
            AiProviderProtocol.ANTHROPIC_MESSAGES,
            '{"content":[{"type":"text","text":"{}"}],"usage":{"input_tokens":2,"output_tokens":3}}',
            {"input": 2, "output": 3, "total": 5},
        ),
    ],
)
def test_protocol_adapters_parse_usage(
    protocol: AiProviderProtocol, body: str, expected: dict[str, int]
) -> None:
    assert AiProviderClient._parse(protocol, body) == ("{}", expected)


def test_provider_http_errors_do_not_collapse_to_unreachable() -> None:
    assert AiProviderClient._http_error(401).code == "AUTHENTICATION_FAILED"
    assert AiProviderClient._http_error(429).retryable
    assert AiProviderClient._http_error(500).code == "HTTP_5XX"
    with pytest.raises(ProviderRequestError, match="PROVIDER_INVALID_OUTPUT"):
        AiProviderClient._parse(AiProviderProtocol.OPENAI_RESPONSES, "{}")
