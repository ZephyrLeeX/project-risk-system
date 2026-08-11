from __future__ import annotations

import pytest
from pydantic import ValidationError

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
