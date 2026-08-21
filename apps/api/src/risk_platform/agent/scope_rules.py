"""Runtime scope-rule cache: PostgreSQL truth, Redis notification, TTL fallback.

Admin-managed rules in ``agent_scope_rules`` are the operational tuning surface
of the three-state layer-1 scope gate.  PostgreSQL is the single source of
truth; this module keeps a per-process immutable snapshot of compiled rules so
``decide()`` never queries the database on the agent path.

Consistency model:

* Every rule mutation commits with an atomic ``revision + 1`` on the
  single-row ``agent_scope_rule_revision`` table (same transaction), then
  publishes the new revision on the Redis channel ``agent.scope.rules.changed``.
* API processes run a pub/sub listener (immediate reload) *and* a TTL poll
  (self-heal for lost Redis events, default 15s).
* Worker processes — where ``decide()`` actually runs — do not run background
  listeners; ``DynamicScopePolicy.prepare()`` TTL-probes the revision at the
  top of every execution instead, so a rule change becomes visible without a
  restart within one poll interval.
* Redis being unavailable only degrades notification latency (poll-only);
  PostgreSQL being unavailable means the last good snapshot keeps serving
  (serve-stale).  Neither is ever on the synchronous decision path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.agent.models import (
    AgentScopeRule,
    AgentScopeRuleRevision,
)
from risk_platform.agent.scope import (
    ScopeDecision,
    ScopeDecisionSource,
    ScopeEvaluation,
    ScopeMatch,
    ScopePolicy,
    ScopeRuleMatchType,
    normalize_scope_text,
)

logger = logging.getLogger(__name__)

SCOPE_RULES_CHANNEL = "agent.scope.rules.changed"
MAX_RULE_PATTERN_LENGTH = 200

# Match-type specificity for same-priority ordering: a precise EXACT rule
# wins over a broader PHRASE rule so a specific ALLOW exception can override
# a generic BLOCK at the same priority.
_SPECIFICITY_RANK: dict[ScopeRuleMatchType, int] = {
    ScopeRuleMatchType.EXACT: 0,
    ScopeRuleMatchType.PHRASE: 1,
}

_EMPTY_SNAPSHOT_REVISION = -1


@dataclass(frozen=True, slots=True)
class CompiledScopeRule:
    rule_id: str
    name: str
    decision: ScopeDecision
    match_type: ScopeRuleMatchType
    priority: int
    pattern: str  # already normalized by normalize_scope_text

    def matches(self, normalized_text: str) -> bool:
        if self.match_type is ScopeRuleMatchType.EXACT:
            return self.pattern == normalized_text
        return self.pattern in normalized_text


def _rule_sort_key(rule: CompiledScopeRule) -> tuple[int, int, int, str]:
    """priority desc → specificity (EXACT < PHRASE) → BLOCK before ALLOW → name."""

    return (
        -rule.priority,
        _SPECIFICITY_RANK[rule.match_type],
        0 if rule.decision is ScopeDecision.BLOCK else 1,
        rule.name,
    )


def validate_scope_rule_pattern(pattern: str) -> str:
    """Validate one pattern with the exact runtime rules; returns the normal form.

    Single source of truth shared by the admin write paths (create/update/
    preview) and the runtime compile: NFKC → trim → whitespace collapse, then
    the normalized form must be non-empty and within ``MAX_RULE_PATTERN_LENGTH``.
    Raises ``ValueError`` otherwise — so a pattern the API accepted can never
    be silently skipped by ``ScopeRuleStore.refresh`` (NFKC can expand one
    character into many, making the raw-length request check insufficient).
    """

    normalized = normalize_scope_text(pattern)
    if not normalized or len(normalized) > MAX_RULE_PATTERN_LENGTH:
        raise ValueError(f"invalid scope rule pattern: {pattern!r}")
    return normalized


def compile_scope_rule(
    *,
    rule_id: str,
    name: str,
    decision: ScopeDecision,
    match_type: ScopeRuleMatchType,
    priority: int,
    pattern: str,
) -> CompiledScopeRule:
    """Normalize and compile one rule; raises ``ValueError`` on an unusable pattern.

    Shared by the live snapshot load (``ScopeRuleStore.refresh``) and the
    admin /test preview so both compile rules identically.
    """

    return CompiledScopeRule(
        rule_id=rule_id,
        name=name,
        decision=decision,
        match_type=match_type,
        priority=priority,
        pattern=validate_scope_rule_pattern(pattern),
    )


@dataclass(frozen=True, slots=True)
class ScopeRuleSnapshot:
    """Immutable compiled view of all enabled, non-deleted rules."""

    rules: tuple[CompiledScopeRule, ...]
    revision: int = _EMPTY_SNAPSHOT_REVISION  # -1 until the first PG load


EMPTY_SNAPSHOT = ScopeRuleSnapshot(rules=(), revision=_EMPTY_SNAPSHOT_REVISION)


def evaluate_with_snapshot(message: str, snapshot: ScopeRuleSnapshot) -> ScopeEvaluation:
    """Runtime rules first (first match in sort order wins), then the builtin baseline.

    Runtime rules are administrative overrides: a runtime hit — including a
    BLOCK rule — always wins over the builtin baseline, so an admin can
    deliberately override builtin ALLOW.  This is intended behaviour, not a
    bug; the admin /test preview surface and the broad-BLOCK warnings make the
    override power visible before a rule goes live.
    """

    text = normalize_scope_text(message)
    if not text:
        return ScopeEvaluation(ScopeDecision.BLOCK, ScopeDecisionSource.DEFAULT, None)
    for rule in snapshot.rules:
        if rule.matches(text):
            return ScopeEvaluation(
                rule.decision,
                ScopeDecisionSource.RUNTIME_RULE,
                ScopeMatch(rule.rule_id, rule.name, rule.match_type, rule.priority),
            )
    return ScopePolicy().evaluate_normalized(text)


def compose_preview_snapshot(
    snapshot: ScopeRuleSnapshot,
    candidate: CompiledScopeRule,
    *,
    exclude_rule_id: str | None = None,
) -> ScopeRuleSnapshot:
    """Combine the live snapshot with one preview candidate for /test.

    Uses exactly the production ``_rule_sort_key`` ordering (priority desc →
    specificity → BLOCK > ALLOW → stable name tie breaker), so the preview
    shows what Layer 1 *would* decide if the candidate were enabled —
    including losing to or overriding an existing live rule.  Pure function:
    it never mutates the store's live snapshot.
    """

    rules = [
        rule
        for rule in snapshot.rules
        if exclude_rule_id is None or rule.rule_id != exclude_rule_id
    ]
    rules.append(candidate)
    rules.sort(key=_rule_sort_key)
    return ScopeRuleSnapshot(tuple(rules), snapshot.revision)


class ScopeRuleNotifier(Protocol):
    """Transport for cross-process invalidation events (Redis pub/sub)."""

    def subscribe(self, callback: Callable[[str], Awaitable[None]]) -> None: ...

    async def publish(self, revision: int) -> None: ...

    async def run(self) -> None: ...

    async def aclose(self) -> None: ...


class RedisScopeRuleNotifier:
    """Redis pub/sub notifier on the existing broker connection settings.

    The client connects lazily; every transport failure is retried with
    backoff and never propagates into the agent decision path.  Redis here is
    a *notification accelerator only* — the TTL poll guarantees eventual
    consistency without it.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
        self._client: object | None = None
        self._callbacks: list[Callable[[str], Awaitable[None]]] = []
        self._closed = False

    def subscribe(self, callback: Callable[[str], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    async def _ensure_client(self) -> object:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url)  # type: ignore[no-untyped-call]
        return self._client

    async def publish(self, revision: int) -> None:
        client = await self._ensure_client()
        await client.publish(SCOPE_RULES_CHANNEL, str(revision))  # type: ignore[attr-defined]

    async def run(self) -> None:
        """Listen for invalidation events until cancelled; failures retry with backoff."""

        backoff = 1.0
        while not self._closed:
            try:
                client = await self._ensure_client()
                pubsub = client.pubsub()  # type: ignore[attr-defined]
                await pubsub.subscribe(SCOPE_RULES_CHANNEL)
                async for message in pubsub.listen():
                    if self._closed:
                        break
                    if not isinstance(message, dict) or message.get("type") != "message":
                        continue
                    payload = message.get("data")
                    await self._dispatch("" if payload is None else str(payload))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "agent scope notify status=disconnected error_class=%s", type(error).__name__
                )
            if self._closed:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _dispatch(self, payload: str) -> None:
        for callback in self._callbacks:
            try:
                await callback(payload)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "agent scope notify status=callback_failed error_class=%s",
                    type(error).__name__,
                )

    async def aclose(self) -> None:
        self._closed = True
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()  # type: ignore[attr-defined]
            except Exception as error:
                logger.warning(
                    "agent scope notify status=close_failed error_class=%s", type(error).__name__
                )


class ScopeRuleStore:
    """Per-process compiled-rule cache backed by PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        notifier: ScopeRuleNotifier | None = None,
        *,
        background: bool = False,
        poll_interval: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._notifier = notifier
        self._background = background
        self._poll_interval = poll_interval
        self._clock = clock
        self._snapshot = EMPTY_SNAPSHOT
        self._last_probe = float("-inf")
        self._tasks: list[asyncio.Task[None]] = []
        self._reload_lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Tolerant initial load: on PG failure the empty (builtin-only)
        snapshot keeps decisions working."""

        try:
            await self.refresh()
        except Exception as error:
            logger.warning(
                "agent scope refresh status=initial_failed error_class=%s",
                type(error).__name__,
            )
        if not self._background:
            return
        if self._notifier is not None:
            self._notifier.subscribe(self._on_event)
            self._tasks.append(asyncio.create_task(self._run_listener()))
        self._tasks.append(asyncio.create_task(self._run_poll_loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self._notifier is not None:
            await self._notifier.aclose()

    async def _run_listener(self) -> None:
        assert self._notifier is not None
        await self._notifier.run()

    async def _run_poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self.probe()
            except Exception as error:
                logger.warning(
                    "agent scope refresh status=stale error_class=%s", type(error).__name__
                )

    # -- reads -------------------------------------------------------------

    def get_snapshot(self) -> ScopeRuleSnapshot:
        return self._snapshot

    async def refresh(self) -> int:
        """Load, compile, and atomically swap the snapshot; returns the revision."""

        async with self._reload_lock:
            async with self._session_factory() as session:
                revision_row = await session.execute(
                    select(AgentScopeRuleRevision.revision).where(AgentScopeRuleRevision.id == 1)
                )
                revision = revision_row.scalar_one_or_none()
                if revision is None:
                    # Tolerate a missing revision row (fresh/broken DB): treat
                    # as revision 0 rather than failing the whole load.
                    revision = 0
                result = await session.execute(
                    select(AgentScopeRule).where(
                        AgentScopeRule.enabled.is_(True),
                        AgentScopeRule.deletedAt.is_(None),
                    )
                )
                rows = list(result.scalars().all())
            rules: list[CompiledScopeRule] = []
            for row in rows:
                try:
                    rules.append(
                        compile_scope_rule(
                            rule_id=str(row.id),
                            name=row.name,
                            decision=ScopeDecision(row.decision.value),
                            match_type=row.matchType,
                            priority=row.priority,
                            pattern=row.pattern,
                        )
                    )
                except ValueError:
                    logger.warning(
                        "agent scope rule skipped rule_id=%s reason=invalid_pattern", row.id
                    )
                    continue
            rules.sort(key=_rule_sort_key)
            self._snapshot = ScopeRuleSnapshot(tuple(rules), revision)
            self._last_probe = self._clock()
            logger.info(
                "agent scope reload revision=%s rule_count=%d status=success",
                revision,
                len(rules),
            )
            return revision

    async def maybe_refresh(self) -> None:
        """TTL-gated consistency check for the hot path (worker ``prepare``)."""

        if self._clock() - self._last_probe < self._poll_interval:
            return
        try:
            await self.probe()
        except Exception as error:
            logger.warning(
                "agent scope refresh status=stale error_class=%s", type(error).__name__
            )
            # Back off so a failing database is not probed on every message.
            self._last_probe = self._clock()

    async def probe(self) -> None:
        """Reload only when the single-row revision moved."""

        async with self._session_factory() as session:
            row = await session.execute(
                select(AgentScopeRuleRevision.revision).where(AgentScopeRuleRevision.id == 1)
            )
            revision = row.scalar_one_or_none()
        revision = 0 if revision is None else revision
        self._last_probe = self._clock()
        if revision != self._snapshot.revision:
            await self.refresh()

    # -- writes / notification ---------------------------------------------

    async def notify_changed(self) -> None:
        """Refresh locally (this process is immediately consistent) and publish.

        Called by the admin service after its mutation transaction committed
        (the revision bump happens inside that transaction).  Any failure here
        is logged, never raised: the TTL poll on every instance guarantees the
        change propagates regardless.
        """

        try:
            revision = await self.refresh()
        except Exception as error:
            logger.warning(
                "agent scope notify status=local_refresh_failed error_class=%s",
                type(error).__name__,
            )
            return
        if self._notifier is None:
            return
        try:
            await self._notifier.publish(revision)
        except Exception as error:
            logger.warning(
                "agent scope notify status=publish_failed error_class=%s", type(error).__name__
            )

    async def _on_event(self, payload: str) -> None:
        """Handle an invalidation event from the pub/sub listener."""

        text = payload.strip()
        if text.isdigit() and int(text) == self._snapshot.revision:
            return  # already up to date (e.g. our own publish echoed back)
        try:
            await self.refresh()
        except Exception as error:
            logger.warning(
                "agent scope refresh status=stale error_class=%s", type(error).__name__
            )


class DynamicScopePolicy(ScopePolicy):
    """Layer-1 policy combining runtime rules with the builtin baseline."""

    def __init__(self, store: ScopeRuleStore) -> None:
        self._store = store

    async def prepare(self) -> None:
        await self._store.maybe_refresh()

    def evaluate(self, message: str) -> ScopeEvaluation:
        return evaluate_with_snapshot(message, self._store.get_snapshot())


__all__ = [
    "EMPTY_SNAPSHOT",
    "SCOPE_RULES_CHANNEL",
    "CompiledScopeRule",
    "DynamicScopePolicy",
    "RedisScopeRuleNotifier",
    "ScopeRuleNotifier",
    "ScopeRuleSnapshot",
    "ScopeRuleStore",
    "compile_scope_rule",
    "compose_preview_snapshot",
    "evaluate_with_snapshot",
    "validate_scope_rule_pattern",
]
