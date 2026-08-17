from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.v2_adapter import (
    AiProviderAdapter,
    ProviderCandidate,
    ProviderCandidatesExhausted,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorClassification,
    ProviderFinishReason,
    ProviderMessage,
    ProviderModelInfo,
    ProviderRole,
    ProviderTokenUsage,
    ProviderType,
)
from risk_platform.ai_providers.v2_service import ProviderV2Runtime


def _candidate(model: str) -> ProviderCandidate:
    return ProviderCandidate(
        uuid4(),
        "DeepSeek Official",
        ProviderType.DEEPSEEK_OFFICIAL,
        uuid4(),
        model,
        60,
        f"encrypted-{model}",
    )


def _success(text: str = "ok") -> ProviderChatResponse:
    return ProviderChatResponse(
        text,
        (),
        ProviderFinishReason.STOP,
        ProviderTokenUsage(1, 1, 2),
        10,
    )


def _error(
    classification: ProviderErrorClassification,
    *,
    retryable: bool,
    failover: bool,
    retry_after: float | None = None,
) -> ProviderError:
    return ProviderError(
        classification,
        retryable=retryable,
        failover_allowed=failover,
        retry_after_seconds=retry_after,
    )


class FakeAdapter(AiProviderAdapter):
    def __init__(
        self,
        scripts: dict[str, list[ProviderChatResponse | ProviderError]],
        on_call: Callable[[str], None] | None = None,
    ) -> None:
        self.scripts = scripts
        self.on_call = on_call
        self.calls: list[str] = []

    async def list_models(
        self, encrypted_api_key: str, timeout_seconds: int
    ) -> tuple[ProviderModelInfo, ...]:
        del encrypted_api_key, timeout_seconds
        return ()

    async def chat(
        self, candidate: ProviderCandidate, request: ProviderChatRequest
    ) -> ProviderChatResponse:
        del request
        self.calls.append(candidate.model_name)
        if self.on_call is not None:
            self.on_call(candidate.model_name)
        outcome = self.scripts[candidate.model_name].pop(0)
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome


class MemoryRuntime(ProviderV2Runtime):
    def __init__(
        self,
        source: list[ProviderCandidate],
        adapter: FakeAdapter,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        super().__init__(
            cast(async_sessionmaker[AsyncSession], object()),
            adapter,
            sleep=sleep,
            jitter=lambda _start, _end: 0.0,
        )
        self.source = source
        self.successes = 0
        self.failures: list[ProviderErrorClassification] = []

    async def candidate_snapshot(self) -> tuple[ProviderCandidate, ...]:
        return tuple(self.source)

    async def _record_success(
        self, candidate: ProviderCandidate, response: ProviderChatResponse
    ) -> None:
        del candidate, response
        self.successes += 1

    async def _record_failure(
        self, candidate: ProviderCandidate, error: ProviderError, started: float
    ) -> None:
        del candidate, started
        self.failures.append(error.classification)

    async def _record_health_error(
        self, candidate: ProviderCandidate, error: ProviderError
    ) -> None:
        del candidate, error


REQUEST = ProviderChatRequest((ProviderMessage(ProviderRole.USER, "hello"),))


@pytest.mark.parametrize(
    "classification",
    [
        ProviderErrorClassification.NETWORK,
        ProviderErrorClassification.TIMEOUT,
        ProviderErrorClassification.RATE_LIMITED,
        ProviderErrorClassification.TRANSIENT_SERVER,
    ],
)
def test_retryable_error_retries_same_model_before_success(
    classification: ProviderErrorClassification,
) -> None:
    model = _candidate("default")
    adapter = FakeAdapter(
        {"default": [_error(classification, retryable=True, failover=True), _success()]}
    )
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    runtime = MemoryRuntime([model], adapter, sleep)
    result = asyncio.run(runtime.chat(REQUEST))

    assert result.content == "ok"
    assert adapter.calls == ["default", "default"]
    assert sleeps == [0.25]


def test_retry_after_controls_bounded_retry_delay() -> None:
    model = _candidate("default")
    adapter = FakeAdapter(
        {
            "default": [
                _error(
                    ProviderErrorClassification.RATE_LIMITED,
                    retryable=True,
                    failover=True,
                    retry_after=7.0,
                ),
                _success(),
            ]
        }
    )
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    asyncio.run(MemoryRuntime([model], adapter, sleep).chat(REQUEST))
    assert sleeps == [7.0]


def test_retry_exhausted_then_failover_to_next_model() -> None:
    first, second = _candidate("default"), _candidate("next")

    def transient() -> ProviderError:
        return _error(
            ProviderErrorClassification.TRANSIENT_SERVER, retryable=True, failover=True
        )
    adapter = FakeAdapter(
        {"default": [transient(), transient(), transient()], "next": [_success("next-ok")]}
    )

    async def sleep(_delay: float) -> None:
        return None

    result = asyncio.run(MemoryRuntime([first, second], adapter, sleep).chat(REQUEST))
    assert result.content == "next-ok"
    assert adapter.calls == ["default", "default", "default", "next"]


def test_model_not_found_fails_over_without_retry() -> None:
    first, second = _candidate("missing"), _candidate("next")
    adapter = FakeAdapter(
        {
            "missing": [
                _error(
                    ProviderErrorClassification.MODEL_NOT_FOUND,
                    retryable=False,
                    failover=True,
                )
            ],
            "next": [_success()],
        }
    )

    async def sleep(_delay: float) -> None:
        return None

    asyncio.run(MemoryRuntime([first, second], adapter, sleep).chat(REQUEST))
    assert adapter.calls == ["missing", "next"]


@pytest.mark.parametrize(
    "classification",
    [
        ProviderErrorClassification.AUTHENTICATION,
        ProviderErrorClassification.PERMISSION,
        ProviderErrorClassification.INVALID_REQUEST,
        ProviderErrorClassification.MALFORMED_RESPONSE,
        ProviderErrorClassification.PROTOCOL,
        ProviderErrorClassification.CREDENTIAL_UNAVAILABLE,
    ],
)
def test_non_failover_error_never_calls_next_model(
    classification: ProviderErrorClassification,
) -> None:
    first, second = _candidate("default"), _candidate("next")
    adapter = FakeAdapter(
        {
            "default": [_error(classification, retryable=False, failover=False)],
            "next": [_success()],
        }
    )

    async def sleep(_delay: float) -> None:
        return None

    with pytest.raises(ProviderError) as caught:
        asyncio.run(MemoryRuntime([first, second], adapter, sleep).chat(REQUEST))
    assert caught.value.classification is classification
    assert adapter.calls == ["default"]


def test_all_candidate_models_fail_with_typed_exhausted_error() -> None:
    first, second = _candidate("first"), _candidate("second")

    def missing() -> ProviderError:
        return _error(
            ProviderErrorClassification.MODEL_NOT_FOUND, retryable=False, failover=True
        )
    adapter = FakeAdapter({"first": [missing()], "second": [missing()]})

    async def sleep(_delay: float) -> None:
        return None

    with pytest.raises(ProviderCandidatesExhausted) as caught:
        asyncio.run(MemoryRuntime([first, second], adapter, sleep).chat(REQUEST))
    assert caught.value.classification is ProviderErrorClassification.MODEL_NOT_FOUND
    assert adapter.calls == ["first", "second"]


def test_candidate_snapshot_remains_stable_during_call_and_refreshes_next_call() -> None:
    first, second = _candidate("first"), _candidate("second")
    source = [first, second]

    def reorder(model: str) -> None:
        if model == "first":
            source[:] = [second, first]

    adapter = FakeAdapter(
        {
            "first": [
                _error(
                    ProviderErrorClassification.MODEL_NOT_FOUND,
                    retryable=False,
                    failover=True,
                ),
                _success("first-second-turn"),
            ],
            "second": [_success("second-first-turn"), _success("second-new-turn")],
        },
        on_call=reorder,
    )

    async def sleep(_delay: float) -> None:
        return None

    runtime = MemoryRuntime(source, adapter, sleep)
    first_result = asyncio.run(runtime.chat(REQUEST))
    second_result = asyncio.run(runtime.chat(REQUEST))

    assert first_result.content == "second-first-turn"
    assert second_result.content == "second-new-turn"
    assert adapter.calls == ["first", "second", "second"]
