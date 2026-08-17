from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
import urllib.error
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import ClassVar, cast
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from risk_platform.ai_providers.v2_adapter import (
    DEEPSEEK_OFFICIAL_ORIGIN,
    MAX_RESPONSE_BYTES,
    AiProviderAdapterRegistry,
    DeepSeekOfficialAdapter,
    DeepSeekOfficialHttpTransport,
    ProviderCandidate,
    ProviderChatRequest,
    ProviderError,
    ProviderErrorClassification,
    ProviderFinishReason,
    ProviderHttpResponse,
    ProviderMessage,
    ProviderRole,
    ProviderToolDefinition,
    ProviderType,
)
from risk_platform.model_types import JSONValue
from risk_platform.shared.crypto import KeyRing, SecretCipher
from risk_platform.shared.outbound import OutboundEndpointGuard, ResolvedEndpoint


class ScriptedTransport:
    def __init__(self, outcomes: list[ProviderHttpResponse | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None, int]] = []

    async def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> ProviderHttpResponse:
        self.calls.append((method, path, headers, body, timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class LocalOfficialGuard:
    def __init__(self, endpoint: ResolvedEndpoint) -> None:
        self.endpoint = endpoint
        self.origins: list[str] = []

    async def resolve_provider(self, origin: str) -> ResolvedEndpoint:
        self.origins.append(origin)
        return self.endpoint

    async def revalidate(self, endpoint: ResolvedEndpoint) -> ResolvedEndpoint:
        assert endpoint is self.endpoint
        return endpoint


class FakeDeepSeekHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str, str, dict[str, object] | None]]] = []
    redirect_models: ClassVar[bool] = False

    def do_GET(self) -> None:
        self.requests.append(("GET", self.path, self.headers["Authorization"], None))
        if self.redirect_models:
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(200, {"object": "list", "data": [{"id": "deepseek-chat"}]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.requests.append(("POST", self.path, self.headers["Authorization"], payload))
        self._json(
            200,
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "wire answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _self_signed_certificate(directory: Path, hostname: str) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / "fake-deepseek.crt"
    key_path = directory / "fake-deepseek.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


def _cipher() -> SecretCipher:
    return SecretCipher(KeyRing(active_version="v1", keys={"v1": b"v" * 32}))


def _candidate(cipher: SecretCipher, model: str = "configured-model") -> ProviderCandidate:
    return ProviderCandidate(
        uuid4(),
        "DeepSeek Official",
        ProviderType.DEEPSEEK_OFFICIAL,
        uuid4(),
        model,
        23,
        cipher.encrypt("sk-test-secret-value").envelope,
    )


def _response(status: int, value: object, **headers: str) -> ProviderHttpResponse:
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return ProviderHttpResponse(status, body, headers)


def test_models_contract_uses_fixed_relative_path_and_normalizes_ids() -> None:
    cipher = _cipher()
    transport = ScriptedTransport(
        [_response(200, {"object": "list", "data": [{"id": "deepseek-chat"}]})]
    )
    adapter = DeepSeekOfficialAdapter(cipher, transport)
    encrypted = cipher.encrypt("sk-test-secret-value").envelope

    models = asyncio.run(adapter.list_models(encrypted, 17))

    assert [model.id for model in models] == ["deepseek-chat"]
    method, path, headers, body, timeout = transport.calls[0]
    assert (method, path, body, timeout) == ("GET", "/models", None, 17)
    assert headers["Authorization"] == "Bearer sk-test-secret-value"


def test_fake_https_deepseek_server_exercises_tls_wire_and_both_official_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_hostname = "fake-deepseek.invalid"
    certificate_path, key_path = _self_signed_certificate(tmp_path, fake_hostname)
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDeepSeekHandler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    FakeDeepSeekHandler.requests = []
    FakeDeepSeekHandler.redirect_models = False
    thread.start()
    try:
        port = int(server.server_address[1])
        endpoint = ResolvedEndpoint(
            "provider",
            fake_hostname,
            port,
            (ip_address("127.0.0.1"),),
            f"https://{fake_hostname}:{port}",
        )
        guard = LocalOfficialGuard(endpoint)
        cipher = _cipher()
        transport = DeepSeekOfficialHttpTransport(cast(OutboundEndpointGuard, guard))
        adapter = DeepSeekOfficialAdapter(cipher, transport)
        encrypted = cipher.encrypt("sk-test-secret-value").envelope

        models = asyncio.run(adapter.list_models(encrypted, 5))
        response = asyncio.run(
            adapter.chat(
                _candidate(cipher, "wire-model"),
                ProviderChatRequest((ProviderMessage(ProviderRole.USER, "wire request"),)),
            )
        )

        assert [model.id for model in models] == ["deepseek-chat"]
        assert response.content == "wire answer"
        assert guard.origins == [DEEPSEEK_OFFICIAL_ORIGIN, DEEPSEEK_OFFICIAL_ORIGIN]
        assert [(item[0], item[1]) for item in FakeDeepSeekHandler.requests] == [
            ("GET", "/models"),
            ("POST", "/chat/completions"),
        ]
        assert all(
            item[2] == "Bearer sk-test-secret-value" for item in FakeDeepSeekHandler.requests
        )
        assert FakeDeepSeekHandler.requests[1][3] == {
            "model": "wire-model",
            "messages": [{"role": "user", "content": "wire request"}],
        }
        FakeDeepSeekHandler.redirect_models = True
        with pytest.raises(ProviderError) as redirected:
            asyncio.run(adapter.list_models(encrypted, 5))
        assert redirected.value.classification is ProviderErrorClassification.INVALID_REQUEST
        assert [item[1] for item in FakeDeepSeekHandler.requests] == [
            "/models",
            "/chat/completions",
            "/models",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_v1_registry_contains_only_deepseek_official() -> None:
    adapter = DeepSeekOfficialAdapter(_cipher(), ScriptedTransport([]))
    registry = AiProviderAdapterRegistry({ProviderType.DEEPSEEK_OFFICIAL: adapter})
    assert registry.provider_types == (ProviderType.DEEPSEEK_OFFICIAL,)
    assert registry.adapter_for(ProviderType.DEEPSEEK_OFFICIAL) is adapter
    with pytest.raises(ValueError, match="PROVIDER_V2_REGISTRY_INVALID"):
        AiProviderAdapterRegistry({})


@pytest.mark.parametrize(
    ("failure", "classification"),
    [
        (TimeoutError(), ProviderErrorClassification.TIMEOUT),
        (urllib.error.URLError("unreachable"), ProviderErrorClassification.NETWORK),
    ],
)
def test_official_transport_normalizes_timeout_and_network_errors(
    failure: Exception, classification: ProviderErrorClassification
) -> None:
    async def resolver(_host: str, _port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)

    transport = DeepSeekOfficialHttpTransport(OutboundEndpointGuard(resolver=resolver))

    def fail(*_args: object) -> ProviderHttpResponse:
        raise failure

    transport._request_sync = fail  # type: ignore[assignment]
    with pytest.raises(ProviderError) as caught:
        asyncio.run(transport.request("GET", "/models", {}, None, 1))
    assert caught.value.classification is classification
    assert caught.value.retryable
    assert caught.value.failover_allowed


def test_chat_normalizes_assistant_text_and_usage_without_wire_fields() -> None:
    cipher = _cipher()
    transport = ScriptedTransport(
        [
            _response(
                200,
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "answer"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
                },
            )
        ]
    )
    adapter = DeepSeekOfficialAdapter(cipher, transport)

    result = asyncio.run(
        adapter.chat(
            _candidate(cipher),
            ProviderChatRequest((ProviderMessage(ProviderRole.USER, "hello"),)),
        )
    )

    assert result.content == "answer"
    assert result.tool_calls == ()
    assert result.finish_reason is ProviderFinishReason.STOP
    assert result.usage.total_tokens == 5
    payload = json.loads(transport.calls[0][3] or b"{}")
    assert payload["model"] == "configured-model"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]


def test_chat_native_tool_calls_are_normalized_to_typed_provider_dto() -> None:
    cipher = _cipher()
    transport = ScriptedTransport(
        [
            _response(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "project_search",
                                            "arguments": '{"keyword":"锡山"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {},
                },
            )
        ]
    )
    adapter = DeepSeekOfficialAdapter(cipher, transport)
    schema: Mapping[str, JSONValue] = {
        "type": "object",
        "properties": {"keyword": {"type": "string"}},
    }

    result = asyncio.run(
        adapter.chat(
            _candidate(cipher, "admin-configured-model"),
            ProviderChatRequest(
                (ProviderMessage(ProviderRole.USER, "find"),),
                (ProviderToolDefinition("project_search", "search", schema),),
            ),
        )
    )

    assert result.finish_reason is ProviderFinishReason.TOOL_CALLS
    assert result.tool_calls[0].name == "project_search"
    assert result.tool_calls[0].arguments == {"keyword": "锡山"}
    payload = json.loads(transport.calls[0][3] or b"{}")
    assert payload["model"] == "admin-configured-model"
    assert payload["tools"][0]["function"]["parameters"] == schema


@pytest.mark.parametrize(
    ("status", "classification", "retryable", "failover"),
    [
        (400, ProviderErrorClassification.INVALID_REQUEST, False, False),
        (401, ProviderErrorClassification.AUTHENTICATION, False, False),
        (403, ProviderErrorClassification.PERMISSION, False, False),
        (404, ProviderErrorClassification.MODEL_NOT_FOUND, False, True),
        (408, ProviderErrorClassification.TIMEOUT, True, True),
        (429, ProviderErrorClassification.RATE_LIMITED, True, True),
        (500, ProviderErrorClassification.TRANSIENT_SERVER, True, True),
        (502, ProviderErrorClassification.TRANSIENT_SERVER, True, True),
        (503, ProviderErrorClassification.TRANSIENT_SERVER, True, True),
        (599, ProviderErrorClassification.TRANSIENT_SERVER, True, True),
    ],
)
def test_http_error_matrix(
    status: int,
    classification: ProviderErrorClassification,
    retryable: bool,
    failover: bool,
) -> None:
    cipher = _cipher()
    transport = ScriptedTransport([_response(status, {"sensitive": "raw-response"})])
    adapter = DeepSeekOfficialAdapter(cipher, transport)

    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            adapter.chat(
                _candidate(cipher),
                ProviderChatRequest((ProviderMessage(ProviderRole.USER, "secret prompt"),)),
            )
        )

    assert caught.value.classification is classification
    assert caught.value.retryable is retryable
    assert caught.value.failover_allowed is failover
    assert str(caught.value) == classification.value
    assert "raw-response" not in str(caught.value)
    assert "secret prompt" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"choices":[]}',
        b'{"choices":[{"message":{"content":null},"finish_reason":"stop"}]}',
        b'{"choices":[{"message":{"tool_calls":[{"id":"x","type":"function","function":{"name":"f","arguments":"[]"}}]},"finish_reason":"tool_calls"}]}',
    ],
)
def test_malformed_provider_response_never_crosses_adapter(body: bytes) -> None:
    cipher = _cipher()
    adapter = DeepSeekOfficialAdapter(cipher, ScriptedTransport([_response(200, body)]))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            adapter.chat(
                _candidate(cipher),
                ProviderChatRequest((ProviderMessage(ProviderRole.USER, "hello"),)),
            )
        )
    assert caught.value.classification is ProviderErrorClassification.MALFORMED_RESPONSE
    assert not caught.value.failover_allowed


def test_response_body_limit_is_fail_closed() -> None:
    cipher = _cipher()
    adapter = DeepSeekOfficialAdapter(
        cipher, ScriptedTransport([_response(200, b"x" * (MAX_RESPONSE_BYTES + 1))])
    )
    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter.list_models(_candidate(cipher).encrypted_api_key, 10))
    assert caught.value.classification is ProviderErrorClassification.MALFORMED_RESPONSE


@pytest.mark.parametrize("invalid_usage", [1.5, "2", True])
def test_usage_rejects_non_json_integer_values(invalid_usage: object) -> None:
    cipher = _cipher()
    adapter = DeepSeekOfficialAdapter(
        cipher,
        ScriptedTransport(
            [
                _response(
                    200,
                    {
                        "choices": [
                            {"message": {"content": "answer"}, "finish_reason": "stop"}
                        ],
                        "usage": {
                            "prompt_tokens": invalid_usage,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    },
                )
            ]
        ),
    )
    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            adapter.chat(
                _candidate(cipher),
                ProviderChatRequest((ProviderMessage(ProviderRole.USER, "hello"),)),
            )
        )
    assert caught.value.classification is ProviderErrorClassification.MALFORMED_RESPONSE


def test_retry_after_is_bounded_and_supports_numeric_header() -> None:
    cipher = _cipher()
    adapter = DeepSeekOfficialAdapter(
        cipher,
        ScriptedTransport([_response(429, {}, **{"Retry-After": "999"})]),
    )
    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter.list_models(_candidate(cipher).encrypted_api_key, 10))
    assert caught.value.retry_after_seconds == 10.0


def test_retry_after_header_lookup_is_case_insensitive() -> None:
    cipher = _cipher()
    adapter = DeepSeekOfficialAdapter(
        cipher,
        ScriptedTransport([_response(429, {}, **{"retry-after": "3"})]),
    )
    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter.list_models(_candidate(cipher).encrypted_api_key, 10))
    assert caught.value.retry_after_seconds == 3.0


def test_secret_prompt_and_raw_response_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    cipher = _cipher()
    adapter = DeepSeekOfficialAdapter(
        cipher,
        ScriptedTransport([_response(500, {"raw": "provider-sensitive-body"})]),
    )
    with pytest.raises(ProviderError):
        asyncio.run(
            adapter.chat(
                _candidate(cipher),
                ProviderChatRequest((ProviderMessage(ProviderRole.USER, "business prompt"),)),
            )
        )
    logs = caplog.text
    assert "sk-test-secret-value" not in logs
    assert "business prompt" not in logs
    assert "provider-sensitive-body" not in logs
