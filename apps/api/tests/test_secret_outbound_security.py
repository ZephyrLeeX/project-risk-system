from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Sequence
from ipaddress import ip_network
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from risk_platform.shared.crypto import (
    KeyRing,
    LegacySecretFields,
    SecretCipher,
    SecretCryptoError,
    SecretEnvelope,
    mask_secret,
)
from risk_platform.shared.outbound import (
    OutboundEndpointGuard,
    OutboundPolicy,
    OutboundSecurityError,
    ResolvedEndpoint,
    provider_subresource_url,
)


def _key(byte: int) -> bytes:
    return bytes([byte]) * 32


def _cipher(active: str = "2026-08", **keys: bytes) -> SecretCipher:
    material = keys or {"2026-08": _key(8)}
    return SecretCipher(KeyRing(active_version=active, keys=material))


def test_secret_roundtrip_uses_random_nonce_and_safe_mask() -> None:
    cipher = _cipher()
    plaintext = "sk-live-sensitive-8D2F"

    first = cipher.encrypt(plaintext)
    second = cipher.encrypt(plaintext)

    assert first.envelope != second.envelope
    assert plaintext not in first.envelope
    assert first.masked == "••••••••••••8D2F"
    assert cipher.decrypt(first.envelope) == plaintext
    parsed = SecretEnvelope.parse(first.envelope)
    assert parsed.key_version == "2026-08"
    assert len(parsed.nonce) == 12


def test_legacy_secret_roundtrip_uses_no_aad_triplet() -> None:
    cipher = _cipher(active="v1", v1=_key(9))

    fields = cipher.encrypt_legacy("secret")

    assert fields.key_version == "v1"
    assert cipher.decrypt_legacy(fields) == "secret"
    assert "secret" not in repr(fields)


def test_legacy_wrong_key_fails_without_leaking_secret_or_key() -> None:
    plaintext = "mailbox-secret-value"
    fields = _cipher(active="v1", v1=_key(9)).encrypt_legacy(plaintext)

    with pytest.raises(SecretCryptoError) as failure:
        _cipher(active="v1", v1=_key(10)).decrypt_legacy(fields)

    assert str(failure.value) == "SECRET_DECRYPTION_FAILED"
    assert plaintext not in str(failure.value)
    assert base64.b64encode(_key(9)).decode() not in str(failure.value)
    assert fields.ciphertext not in str(failure.value)


def test_rotation_reads_old_key_and_writes_only_active_version() -> None:
    old_cipher = _cipher(active="old", old=_key(1))
    old = old_cipher.encrypt("mail-auth-code-9921").envelope
    rotating = _cipher(active="current", old=_key(1), current=_key(2))

    assert rotating.needs_rotation(old)
    rotated = rotating.rotate(old)

    assert SecretEnvelope.parse(rotated.envelope).key_version == "current"
    assert rotating.decrypt(rotated.envelope) == "mail-auth-code-9921"
    assert not rotating.needs_rotation(rotated.envelope)


def test_missing_key_and_tamper_fail_without_secret_in_error() -> None:
    plaintext = "never-print-this-secret"
    encrypted = _cipher(active="retired", retired=_key(3)).encrypt(plaintext).envelope
    current_only = _cipher(active="current", current=_key(4))

    with pytest.raises(SecretCryptoError) as missing:
        current_only.decrypt(encrypted)
    assert str(missing.value) == "ENCRYPTION_KEY_NOT_FOUND"

    parsed = SecretEnvelope.parse(encrypted)
    tampered = bytearray(parsed.ciphertext)
    tampered[-1] ^= 1
    damaged = SecretEnvelope(parsed.key_version, parsed.nonce, bytes(tampered)).serialize()
    with pytest.raises(SecretCryptoError) as invalid:
        _cipher(active="retired", retired=_key(3)).decrypt(damaged)
    assert str(invalid.value) == "SECRET_DECRYPTION_FAILED"
    assert plaintext not in f"{missing.value} {invalid.value}"


def test_key_ring_loads_base64_keys_from_explicit_files(tmp_path: Path) -> None:
    key_path = tmp_path / "data-encryption-key"
    key_path.write_text(base64.b64encode(_key(5)).decode("ascii") + "\n", encoding="ascii")

    ring = KeyRing.from_files("v5", {"v5": key_path})

    assert ring.key_for("v5") == _key(5)
    assert not hasattr(ring, "environ")


def test_legacy_aes_gcm_triplet_can_be_rotated_to_versioned_envelope() -> None:
    key = _key(7)
    nonce = os.urandom(12)
    plaintext = "legacy-api-key-7A9C"
    combined = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    legacy = LegacySecretFields(
        ciphertext=base64.b64encode(combined[:-16]).decode(),
        iv=base64.b64encode(nonce).decode(),
        auth_tag=base64.b64encode(combined[-16:]).decode(),
        key_version="legacy",
    )
    cipher = _cipher(active="current", legacy=key, current=_key(8))

    rotated = cipher.rotate_legacy(legacy)

    assert SecretEnvelope.parse(rotated.envelope).key_version == "current"
    assert cipher.decrypt(rotated.envelope) == plaintext


@pytest.mark.parametrize("value", ["", "abc", "tiny"])
def test_short_secrets_are_fully_masked(value: str) -> None:
    masked = mask_secret(value)
    assert value == "" or value not in masked
    assert len(masked) >= 8


class StaticResolver:
    def __init__(self, *answers: Sequence[str] | OSError) -> None:
        self._answers = list(answers)

    async def __call__(self, hostname: str, port: int) -> Sequence[str]:
        del hostname, port
        answer = self._answers.pop(0)
        if isinstance(answer, OSError):
            raise answer
        return answer


def test_public_provider_and_imap_targets_return_pinned_addresses() -> None:
    async def scenario() -> None:
        provider = await OutboundEndpointGuard(
            resolver=StaticResolver(("93.184.216.34",))
        ).resolve_provider("https://api.example.com/v1")
        mailbox = await OutboundEndpointGuard(
            resolver=StaticResolver(("1.1.1.1",))
        ).resolve_imap("imap.example.com", 993)

        assert provider.connection_address == "93.184.216.34"
        assert provider.url == "https://api.example.com/v1"
        assert provider_subresource_url(provider, "models") == "https://api.example.com/v1/models"
        assert mailbox.connection_address == "1.1.1.1"
        assert mailbox.hostname == "imap.example.com"

    asyncio.run(scenario())


def test_public_deepseek_provider_needs_no_allowlist() -> None:
    async def scenario() -> None:
        endpoint = await OutboundEndpointGuard(
            resolver=StaticResolver(("47.246.24.173",))
        ).resolve_provider("https://api.deepseek.com")

        assert endpoint.connection_address == "47.246.24.173"
        assert provider_subresource_url(endpoint, "models") == "https://api.deepseek.com/models"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("host", "address"),
    [
        ("localhost", "127.0.0.1"),
        ("provider.example", "10.2.3.4"),
        ("provider.example", "169.254.169.254"),
        ("provider.example", "::1"),
        ("provider.example", "::ffff:127.0.0.1"),
        ("metadata.google.internal", "8.8.8.8"),
    ],
)
def test_local_private_and_metadata_targets_are_blocked_by_default(
    host: str, address: str
) -> None:
    async def scenario() -> None:
        guard = OutboundEndpointGuard(resolver=StaticResolver((address,)))
        with pytest.raises(OutboundSecurityError) as error:
            await guard.resolve_imap(host, 993)
        assert str(error.value) == "OUTBOUND_DESTINATION_FORBIDDEN"
        assert host not in str(error.value)
        assert address not in str(error.value)

    asyncio.run(scenario())


def test_approved_internal_provider_requires_hostname_and_network() -> None:
    async def scenario() -> None:
        policy = OutboundPolicy(
            approved_internal_hostnames=frozenset({"model.ai.internal"}),
            approved_internal_networks=(ip_network("10.20.0.0/16"),),
        )
        approved = await OutboundEndpointGuard(
            policy, StaticResolver(("10.20.3.4",))
        ).resolve_provider("https://model.ai.internal:8443/v1")
        assert approved.connection_address == "10.20.3.4"

        with pytest.raises(OutboundSecurityError):
            await OutboundEndpointGuard(
                policy, StaticResolver(("10.21.3.4",))
            ).resolve_provider("https://model.ai.internal:8443/v1")
        with pytest.raises(OutboundSecurityError):
            await OutboundEndpointGuard(
                policy, StaticResolver(("10.20.3.4",))
            ).resolve_provider("https://unapproved.ai.internal:8443/v1")

    asyncio.run(scenario())


def test_token_longshine_internal_provider_requires_both_explicit_allowlists() -> None:
    async def scenario() -> None:
        policy = OutboundPolicy(
            approved_internal_hostnames=frozenset({"TOKEN.LONGSHINE.COM."}),
            approved_internal_networks=(ip_network("10.0.0.0/8"),),
        )
        guard = OutboundEndpointGuard(policy, StaticResolver(("10.0.0.1",)))
        endpoint = await guard.resolve_provider("https://token.longshine.com:18443")

        assert endpoint.hostname == "token.longshine.com"
        assert provider_subresource_url(endpoint, "models") == (
            "https://token.longshine.com:18443/models"
        )
        for hostname, address in (
            ("token.longshine.com", "172.16.0.1"),
            ("other.longshine.com", "10.0.0.1"),
            ("localhost", "127.0.0.1"),
        ):
            with pytest.raises(OutboundSecurityError) as error:
                await OutboundEndpointGuard(policy, StaticResolver((address,))).resolve_provider(
                    f"https://{hostname}:18443"
                )
            assert error.value.code == "OUTBOUND_DESTINATION_FORBIDDEN"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("host", "address", "network"),
    [
        ("metadata-deny.example", "100.100.100.200", "100.0.0.0/8"),
        ("model.ai.internal", "100.100.100.200", "100.0.0.0/8"),
        ("model.ai.internal", "::ffff:100.100.100.200", "100.0.0.0/8"),
        ("metadata-deny.example", "168.63.129.16", "168.0.0.0/8"),
        ("model.ai.internal", "fd00:ec2::254", "fd00::/8"),
    ],
)
def test_metadata_targets_cannot_be_overridden_by_internal_allowlist(
    host: str, address: str, network: str
) -> None:
    async def scenario() -> None:
        policy = OutboundPolicy(
            approved_internal_hostnames=frozenset({host}),
            approved_internal_networks=(ip_network(network),),
        )
        guard = OutboundEndpointGuard(policy, StaticResolver((address,)))

        with pytest.raises(OutboundSecurityError) as error:
            await guard.resolve_imap(host, 993)
        assert str(error.value) == "OUTBOUND_DESTINATION_FORBIDDEN"

    asyncio.run(scenario())


def test_dns_rebinding_is_rejected_during_preconnect_revalidation() -> None:
    async def scenario() -> None:
        resolver = StaticResolver(("93.184.216.34",), ("127.0.0.1",))
        guard = OutboundEndpointGuard(resolver=resolver)
        endpoint = await guard.resolve_provider("https://api.example.com/v1")

        with pytest.raises(OutboundSecurityError) as error:
            await guard.revalidate(endpoint)
        assert str(error.value) == "OUTBOUND_DESTINATION_FORBIDDEN"

    asyncio.run(scenario())


def test_dns_answer_change_and_redirects_are_rejected() -> None:
    async def scenario() -> None:
        resolver = StaticResolver(("93.184.216.34",), ("93.184.216.35",))
        guard = OutboundEndpointGuard(resolver=resolver)
        endpoint = await guard.resolve_provider("https://api.example.com/v1")
        with pytest.raises(OutboundSecurityError) as changed:
            await guard.revalidate(endpoint)
        assert str(changed.value) == "OUTBOUND_DNS_CHANGED"

        with pytest.raises(OutboundSecurityError) as redirect:
            guard.validate_redirect(endpoint, "https://other.example/steal")
        assert str(redirect.value) == "OUTBOUND_REDIRECT_FORBIDDEN"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path",
    [
        "http://api.example.com/models",
        "https://api.example.com/models",
        "//api.example.com/models",
        "/models",
        "../models",
        "%2e%2e/models",
        "%2fmodels",
        "%2Fmodels",
        "%2f%2fapi.example.com/models",
        "%2F%2Fapi.example.com/models",
        "%252fmodels",
        "%252Fmodels",
        "%252e%252e/models",
        "%252E%252E/models",
        "models?next=http://metadata",
        "models#fragment",
    ],
)
def test_provider_subresource_rejects_non_relative_or_unsafe_paths(path: str) -> None:
    endpoint = ResolvedEndpoint(
        kind="provider",
        hostname="api.example.com",
        port=443,
        addresses=(),
        url="https://api.example.com/v1",
    )

    with pytest.raises(OutboundSecurityError) as error:
        provider_subresource_url(endpoint, path)
    assert str(error.value) == "INVALID_PROVIDER_PATH"


def test_provider_subresource_rejects_https_downgrade_in_source_endpoint() -> None:
    endpoint = ResolvedEndpoint(
        kind="provider",
        hostname="api.example.com",
        port=443,
        addresses=(),
        url="http://api.example.com/v1",
    )

    with pytest.raises(OutboundSecurityError):
        provider_subresource_url(endpoint, "models")


def test_provider_subresource_uses_normalized_https_origin_and_effective_port() -> None:
    endpoint = ResolvedEndpoint(
        kind="provider",
        hostname="api.example.com",
        port=443,
        addresses=(),
        url="https://API.EXAMPLE.COM:443/v1",
    )

    assert provider_subresource_url(endpoint, "chat/completions") == (
        "https://API.EXAMPLE.COM:443/v1/chat/completions"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.example.com/v1",
        "https://api.example.com:0/v1",
        "https://user:secret@api.example.com/v1",
        "https://api.example.com/v1#fragment",
    ],
)
def test_provider_endpoint_errors_are_safe(endpoint: str) -> None:
    async def scenario() -> None:
        guard = OutboundEndpointGuard(resolver=StaticResolver(("93.184.216.34",)))
        with pytest.raises(OutboundSecurityError) as error:
            await guard.resolve_provider(endpoint)
        assert str(error.value) == "INVALID_PROVIDER_ENDPOINT"
        assert endpoint not in str(error.value)
        assert "secret" not in str(error.value)

    asyncio.run(scenario())
