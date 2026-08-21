"""PostgreSQL-backed tests for the runtime scope-rule cache and hot reload."""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.agent.models import AgentScopeRule, AgentScopeRuleDecision
from risk_platform.agent.scope import ScopeDecision, ScopeDecisionSource, ScopeRuleMatchType
from risk_platform.agent.scope_rules import (
    DynamicScopePolicy,
    ScopeRuleStore,
    evaluate_with_snapshot,
)
from risk_platform.db import create_database_engine, create_session_factory, transaction

# Short aliases keep the dense rule-construction lines within the width limit.
_BLOCK = AgentScopeRuleDecision.BLOCK
_ALLOW = AgentScopeRuleDecision.ALLOW

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL Agent validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t040_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()
    engine = create_database_engine(f"{sync_url}?options=-csearch_path%3D{schema}")
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


class _FakeNotifier:
    """In-memory pub/sub bus: publish dispatches to subscribed stores directly."""

    def __init__(self) -> None:
        self.callbacks: list[Callable[[str], Awaitable[None]]] = []
        self.published: list[int] = []
        self.fail_publish = False

    def subscribe(self, callback: Callable[[str], Awaitable[None]]) -> None:
        self.callbacks.append(callback)

    async def publish(self, revision: int) -> None:
        self.published.append(revision)
        if self.fail_publish:
            raise RuntimeError("simulated redis outage")
        for callback in self.callbacks:
            await callback(str(revision))

    async def run(self) -> None:  # pragma: no cover - not used in fake mode
        await asyncio.sleep(0)

    async def aclose(self) -> None:
        return None


class _BrokenSessionFactory:
    """Stand-in session factory that always fails, simulating a PG outage."""

    def __call__(self) -> object:
        raise RuntimeError("simulated postgres outage")


def _rule(
    name: str,
    decision: AgentScopeRuleDecision,
    match_type: ScopeRuleMatchType,
    pattern: str,
    *,
    priority: int = 0,
    enabled: bool = True,
) -> AgentScopeRule:
    return AgentScopeRule(
        id=uuid.uuid4(),
        name=name,
        decision=decision,
        matchType=match_type,
        pattern=pattern,
        priority=priority,
        enabled=enabled,
    )


async def _insert(factory: async_sessionmaker[AsyncSession], *rules: AgentScopeRule) -> None:
    async with transaction(factory) as session:
        session.add_all(rules)
        await session.execute(
            text('UPDATE agent_scope_rule_revision SET revision = revision + 1 WHERE id = 1')
        )


async def _bump_revision_only(factory: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(factory) as session:
        await session.execute(
            text('UPDATE agent_scope_rule_revision SET revision = revision + 1 WHERE id = 1')
        )


def test_runtime_rules_match_exact_and_phrase(database: async_sessionmaker[AsyncSession]) -> None:
    async def scenario() -> None:
        await _insert(
            database,
            _rule("exact-block", _BLOCK, ScopeRuleMatchType.EXACT, "讲个笑话"),
            _rule("phrase-allow", _ALLOW, ScopeRuleMatchType.PHRASE, "大足"),
        )
        store = ScopeRuleStore(database, poll_interval=0)
        policy = DynamicScopePolicy(store)
        await store.refresh()

        exact = policy.evaluate("讲个笑话")
        assert exact.decision is ScopeDecision.BLOCK
        assert exact.source is ScopeDecisionSource.RUNTIME_RULE
        assert exact.match is not None
        assert exact.match.rule_name == "exact-block"
        assert exact.match.match_type is ScopeRuleMatchType.EXACT

        phrase = policy.evaluate("大足这边最近怎么样")
        assert phrase.decision is ScopeDecision.ALLOW
        assert phrase.source is ScopeDecisionSource.RUNTIME_RULE
        assert phrase.match is not None and phrase.match.rule_name == "phrase-allow"

        # EXACT requires full-text equality, not containment: a longer message
        # is not matched by the EXACT rule (here the builtin joke pattern
        # blocks it instead, with no runtime-rule match attached).
        follow = policy.evaluate("再讲个笑话")
        assert follow.decision is ScopeDecision.BLOCK
        assert follow.source is ScopeDecisionSource.BUILTIN
        assert follow.match is None

        # Unmatched text falls through to the builtin baseline / DEFER.
        fallback = policy.evaluate("那南岸呢")
        assert fallback.decision is ScopeDecision.DEFER
        assert fallback.source is ScopeDecisionSource.DEFAULT

    asyncio.run(scenario())


def test_disabled_and_soft_deleted_rules_are_not_loaded(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        await _insert(
            database,
            _rule("disabled-rule", _BLOCK, ScopeRuleMatchType.PHRASE, "独占短语甲",
                  enabled=False),
        )
        store = ScopeRuleStore(database, poll_interval=0)
        await store.refresh()
        # No enabled rule matches; only disabled ones exist for this phrase.
        evaluation = evaluate_with_snapshot("独占短语甲", store.get_snapshot())
        assert evaluation.decision is ScopeDecision.DEFER

        # Soft-deleting an enabled rule removes it after refresh.
        rule = _rule("deleted-rule", _BLOCK, ScopeRuleMatchType.PHRASE, "独占短语乙")
        await _insert(database, rule)
        await store.refresh()
        decision = evaluate_with_snapshot("独占短语乙", store.get_snapshot()).decision
        assert decision is ScopeDecision.BLOCK
        async with transaction(database) as session:
            await session.execute(
                text(
                    f'UPDATE agent_scope_rules SET "deletedAt" = CURRENT_TIMESTAMP '
                    f"WHERE id = '{rule.id}'"
                )
            )
        await _bump_revision_only(database)
        await store.refresh()
        decision = evaluate_with_snapshot("独占短语乙", store.get_snapshot()).decision
        assert decision is ScopeDecision.DEFER

    asyncio.run(scenario())


def test_same_priority_ordering_priority_specificity_then_block(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        await _insert(
            database,
            # Wide BLOCK and specific ALLOW exception at the same priority:
            # the EXACT ALLOW must win over the broader PHRASE BLOCK.
            _rule("wide-block", _BLOCK, ScopeRuleMatchType.PHRASE, "帮我看看星系",
                  priority=10),
            _rule("exact-allow", _ALLOW, ScopeRuleMatchType.EXACT, "帮我看看星系",
                  priority=10),
            # Higher priority always wins over lower.
            _rule("top-block", _BLOCK, ScopeRuleMatchType.PHRASE, "高优先命中",
                  priority=100),
            _rule("low-allow", _ALLOW, ScopeRuleMatchType.PHRASE, "高优先命中",
                  priority=1),
            # Same priority, same specificity: BLOCK wins.
            _rule("tie-block", _BLOCK, ScopeRuleMatchType.PHRASE, "同级冲突短语",
                  priority=5),
            _rule("tie-allow", _ALLOW, ScopeRuleMatchType.PHRASE, "同级冲突短语",
                  priority=5),
        )
        store = ScopeRuleStore(database, poll_interval=0)
        await store.refresh()
        snapshot = store.get_snapshot()

        assert evaluate_with_snapshot("帮我看看星系", snapshot).decision is ScopeDecision.ALLOW
        decision = evaluate_with_snapshot("顺便帮我看看星系运转", snapshot).decision
        assert decision is ScopeDecision.BLOCK
        assert evaluate_with_snapshot("高优先命中", snapshot).decision is ScopeDecision.BLOCK
        assert evaluate_with_snapshot("同级冲突短语", snapshot).decision is ScopeDecision.BLOCK

    asyncio.run(scenario())


def test_hot_path_picks_up_new_rule_without_restart(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        clock = {"now": 0.0}
        store = ScopeRuleStore(database, poll_interval=15.0, clock=lambda: clock["now"])
        policy = DynamicScopePolicy(store)
        await store.refresh()
        assert policy.decide("热加载专用短语") is ScopeDecision.DEFER

        await _insert(
            database,
            _rule("hot-rule", _BLOCK, ScopeRuleMatchType.PHRASE, "热加载专用短语"),
        )
        # Still inside the TTL window: no reload yet.
        clock["now"] = 5.0
        await policy.prepare()
        assert policy.decide("热加载专用短语") is ScopeDecision.DEFER

        # Past the TTL window: prepare() probes the revision and reloads —
        # no restart, no manual refresh.
        clock["now"] = 20.0
        await policy.prepare()
        evaluation = policy.evaluate("热加载专用短语")
        assert evaluation.decision is ScopeDecision.BLOCK
        assert evaluation.source is ScopeDecisionSource.RUNTIME_RULE

    asyncio.run(scenario())


def test_probe_without_revision_change_keeps_snapshot(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        store = ScopeRuleStore(database, poll_interval=0)
        await store.refresh()
        before = store.get_snapshot()
        await store.probe()
        assert store.get_snapshot() is before  # no revision movement, no reload

        await _bump_revision_only(database)
        await store.probe()
        assert store.get_snapshot() is not before

    asyncio.run(scenario())


def test_multi_instance_invalidation_via_notifier(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        bus = _FakeNotifier()
        # api-1 owns the mutation path; api-2 only listens.
        store_a = ScopeRuleStore(database, bus, background=True, poll_interval=999.0)
        store_b = ScopeRuleStore(database, bus, background=True, poll_interval=999.0)
        await store_a.start()
        await store_b.start()
        try:
            await _insert(
                database,
                _rule("multi-rule", _ALLOW, ScopeRuleMatchType.PHRASE, "多实例短语"),
            )
            await store_a.notify_changed()  # admin mutation just committed

            # api-2 reloaded through the notification without any manual refresh.
            evaluation = evaluate_with_snapshot("多实例短语", store_b.get_snapshot())
            assert evaluation.decision is ScopeDecision.ALLOW
            assert evaluation.source is ScopeDecisionSource.RUNTIME_RULE
            assert bus.published  # the invalidation event was broadcast
        finally:
            await store_a.stop()
            await store_b.stop()

    asyncio.run(scenario())


def test_publish_failure_does_not_break_local_refresh(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        bus = _FakeNotifier()
        bus.fail_publish = True
        store = ScopeRuleStore(database, bus, poll_interval=0)
        await _insert(
            database,
            _rule("publish-fail-rule", _BLOCK, ScopeRuleMatchType.PHRASE, "发布失败短语"),
        )
        await store.notify_changed()  # must not raise despite the Redis outage
        evaluation = evaluate_with_snapshot("发布失败短语", store.get_snapshot())
        assert evaluation.decision is ScopeDecision.BLOCK
        assert bus.published  # publish was attempted

    asyncio.run(scenario())


def test_pg_failure_serves_stale_snapshot(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        await _insert(
            database,
            _rule("stale-rule", _BLOCK, ScopeRuleMatchType.PHRASE, "过期快照片语"),
        )
        store = ScopeRuleStore(database, poll_interval=0)
        await store.refresh()
        snapshot_before = store.get_snapshot()

        store._session_factory = _BrokenSessionFactory()  # type: ignore[assignment]
        # maybe_refresh swallows the outage and keeps the last good snapshot.
        await store.maybe_refresh()
        assert store.get_snapshot() is snapshot_before
        decision = evaluate_with_snapshot("过期快照片语", store.get_snapshot()).decision
        assert decision is ScopeDecision.BLOCK

        # A completely fresh store against a dead PG still starts (builtin-only)
        # and decides via the builtin baseline.
        fresh = ScopeRuleStore(_BrokenSessionFactory(), poll_interval=0)  # type: ignore[arg-type]
        await fresh.start()
        policy = DynamicScopePolicy(fresh)
        assert policy.decide("大足这边怎么样") is ScopeDecision.DEFER
        assert policy.decide("当前有哪些高风险") is ScopeDecision.ALLOW

    asyncio.run(scenario())


def test_runtime_block_rule_overrides_builtin_allow(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Frozen semantics: runtime rules are administrative overrides.

    A runtime BLOCK hit wins over the builtin ALLOW baseline (a domain term
    in the message is irrelevant once the override matches).  This is
    intentional admin power, not a bug; the /test preview surface and the
    broad-BLOCK warnings exist to make it visible.
    """

    async def scenario() -> None:
        await _insert(
            database,
            _rule("override-block", _BLOCK, ScopeRuleMatchType.PHRASE, "项目", priority=50),
        )
        store = ScopeRuleStore(database, poll_interval=0)
        await store.refresh()

        # "当前项目有哪些风险" would be builtin ALLOW; the runtime override
        # blocks it because runtime rules run before the builtin baseline.
        evaluation = evaluate_with_snapshot("当前项目有哪些风险", store.get_snapshot())
        assert evaluation.decision is ScopeDecision.BLOCK
        assert evaluation.source is ScopeDecisionSource.RUNTIME_RULE
        assert evaluation.match is not None
        assert evaluation.match.rule_name == "override-block"

        # Without the override the same text is builtin ALLOW.
        empty = ScopeRuleStore(database, poll_interval=0)
        baseline = evaluate_with_snapshot("当前项目有哪些风险", empty.get_snapshot())
        assert baseline.decision is ScopeDecision.ALLOW
        assert baseline.source is ScopeDecisionSource.BUILTIN

    asyncio.run(scenario())


def test_redis_notifier_delivers_invalidation_to_real_redis(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A real Redis pub/sub round trip invalidates a listening store."""


    from risk_platform.agent.scope_rules import RedisScopeRuleNotifier

    broker_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    # Isolated Redis DB so the probe never pollutes the shared broker.
    probe_broker = re.sub(r"/\d+(?:\?.*)?$", "/15", broker_url)
    sync_probe = __import__("redis").from_url(probe_broker)
    try:
        sync_probe.ping()
    except Exception:
        pytest.skip("Redis 7 不可用; T040 pubsub validation 未执行")

    async def scenario() -> None:
        notifier_a = RedisScopeRuleNotifier(probe_broker)
        notifier_b = RedisScopeRuleNotifier(probe_broker)
        store_b = ScopeRuleStore(database, notifier_b, background=True, poll_interval=999.0)
        await store_b.start()
        try:
            await _insert(
                database,
                _rule("redis-rule", _ALLOW, ScopeRuleMatchType.PHRASE, "真实通知短语"),
            )
            await store_b.refresh()  # baseline state before the event
            # Rule removed + revision bumped behind store_b's back; only the
            # Redis event can bring the new state in (TTL is 999s).
            async with transaction(database) as session:
                await session.execute(text('DELETE FROM agent_scope_rules'))
                await session.execute(
                    text(
                        'UPDATE agent_scope_rule_revision '
                        'SET revision = revision + 1 WHERE id = 1'
                    )
                )
            assert evaluate_with_snapshot(
                "真实通知短语", store_b.get_snapshot()
            ).decision is ScopeDecision.ALLOW

            await asyncio.sleep(0.2)  # let the subscription establish
            await notifier_a.publish(store_b.get_snapshot().revision + 1)
            for _ in range(50):
                if evaluate_with_snapshot(
                    "真实通知短语", store_b.get_snapshot()
                ).decision is not ScopeDecision.ALLOW:
                    break
                await asyncio.sleep(0.1)
            assert evaluate_with_snapshot(
                "真实通知短语", store_b.get_snapshot()
            ).decision is ScopeDecision.DEFER  # reloaded without the deleted rule
        finally:
            await store_b.stop()
            await notifier_a.aclose()

    asyncio.run(scenario())
