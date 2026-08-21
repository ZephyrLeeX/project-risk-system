"""`/api/admin/agent/scope-rules` CRUD, permission, and live-effect validation."""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.agent_scope.api import get_admin_agent_scope_service, router
from risk_platform.admin.agent_scope.schemas import ScopeRuleCandidateRule, ScopeRuleTestRequest
from risk_platform.admin.agent_scope.service import AdminAgentScopeRulesService
from risk_platform.admin.models import User
from risk_platform.agent.models import AgentScopeRule
from risk_platform.agent.scope import ScopeDecision, ScopeDecisionSource
from risk_platform.agent.scope_rules import ScopeRuleStore, evaluate_with_snapshot
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.models import AuditLog
from risk_platform.auth.api import current_identity
from risk_platform.auth.schemas import AuthenticatedUser
from risk_platform.auth.service import SessionIdentity
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, transaction
from risk_platform.seed import SeedSettings, seed_reference_data

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = {"origin": "https://web.internal"}


@pytest.fixture(scope="module")
def scope_rules_database() -> Iterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL agent scope rules admin 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t0as_{uuid.uuid4().hex}"
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
        asyncio.run(_seed(factory))
        yield factory
    finally:
        asyncio.run(engine.dispose())
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    from risk_platform.db import transaction

    async with transaction(factory) as session:
        await seed_reference_data(
            session,
            SeedSettings(
                username="admin",
                display_name="管理员",
                password="Seed_Admin9!Pass",
                password_min_length=12,
            ),
        )


async def _identity(
    factory: async_sessionmaker[AsyncSession], permissions: list[str]
) -> SessionIdentity:
    async with factory() as session:
        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        return SessionIdentity(
            session_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            user=AuthenticatedUser(
                id=str(admin.id),
                username=admin.username,
                displayName=admin.displayName,
                departmentName="技术管理部",
                roleCodes=["SYSTEM_ADMIN"],
                permissions=permissions,
                dataScope="ALL",
                mustChangePassword=False,
            ),
        )


async def _client(
    factory: async_sessionmaker[AsyncSession], identity: SessionIdentity
) -> tuple[httpx2.AsyncClient, ScopeRuleStore]:
    store = ScopeRuleStore(factory)  # poll-only; notify_changed refreshes locally
    service = AdminAgentScopeRulesService(factory, store)

    async def override_identity() -> SessionIdentity:
        return identity

    app = create_app(
        Settings(environment="test", cors_origins=("https://web.internal",)),
        AppComposition(
            routers=(router,),
            dependency_overrides={
                current_identity: override_identity,
                get_admin_agent_scope_service: lambda: service,
            },
        ),
    )
    transport = httpx2.ASGITransport(app=app)
    return (
        httpx2.AsyncClient(transport=transport, base_url="https://testserver"),
        store,
    )


def test_scope_rule_crud_lifecycle_audits_and_takes_effect(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, _ = await _client(scope_rules_database, identity)
        try:
            assert (await client.get("/api/admin/agent/scope-rules")).json()["data"] == []

            # New rules are created disabled even when not asked for.
            created = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "拦截闲聊问候",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "早上好",
                    "priority": 10,
                },
            )
            assert created.status_code == 200
            rule = created.json()["data"]
            rule_id = rule["id"]
            assert rule["enabled"] is False
            assert rule["version"] == 1
            assert rule["createdBy"] == identity.user.id

            # A disabled rule must not affect evaluation yet...
            dormant = await client.post(
                "/api/admin/agent/scope-rules/test", headers=ORIGIN, json={"message": "早上好"}
            )
            assert dormant.status_code == 200
            assert dormant.json()["data"]["source"] != "RUNTIME_RULE"

            # ... but it can be previewed by id before being enabled.
            preview = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={"message": "早上好呀", "ruleId": rule_id},
            )
            assert preview.status_code == 200
            preview_data = preview.json()["data"]
            assert preview_data["preview"] is True
            assert preview_data["previewRuleId"] == rule_id
            assert preview_data["decision"] == "BLOCK"
            assert preview_data["source"] == "RUNTIME_RULE"
            assert preview_data["matchedRule"]["id"] == rule_id

            # Enabling takes effect on the next evaluation without a restart.
            enabled = await client.patch(
                f"/api/admin/agent/scope-rules/{rule_id}",
                headers=ORIGIN,
                json={"enabled": True, "version": 1},
            )
            assert enabled.status_code == 200
            assert enabled.json()["data"]["enabled"] is True
            assert enabled.json()["data"]["version"] == 2

            live = await client.post(
                "/api/admin/agent/scope-rules/test", headers=ORIGIN, json={"message": "早上好呀"}
            )
            assert live.status_code == 200
            evaluation = live.json()["data"]
            assert evaluation["decision"] == "BLOCK"
            assert evaluation["source"] == "RUNTIME_RULE"
            assert evaluation["matchedRule"] == {
                "id": rule_id,
                "name": "拦截闲聊问候",
                "matchType": "PHRASE",
                "decision": "BLOCK",
                "priority": 10,
            }

            # Duplicate active names and stale optimistic-lock versions fail.
            duplicate = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "拦截闲聊问候",
                    "decision": "ALLOW",
                    "matchType": "PHRASE",
                    "pattern": "项目",
                },
            )
            assert duplicate.status_code == 409
            stale = await client.patch(
                f"/api/admin/agent/scope-rules/{rule_id}",
                headers=ORIGIN,
                json={"enabled": False, "version": 1},
            )
            assert stale.status_code == 409
            missing = await client.patch(
                f"/api/admin/agent/scope-rules/{uuid.uuid4()}",
                headers=ORIGIN,
                json={"enabled": False, "version": 1},
            )
            assert missing.status_code == 404

            # DELETE requires the current optimistic-lock version and
            # soft-deletes: hidden from the list, no longer evaluated,
            # row retained, and the name becomes reusable.
            stale_delete = await client.delete(
                f"/api/admin/agent/scope-rules/{rule_id}", headers=ORIGIN
            )
            assert stale_delete.status_code == 422  # version is required
            stale_delete = await client.delete(
                f"/api/admin/agent/scope-rules/{rule_id}",
                headers=ORIGIN,
                params={"version": 1},  # version moved to 2 by the PATCH above
            )
            assert stale_delete.status_code == 409
            deleted = await client.delete(
                f"/api/admin/agent/scope-rules/{rule_id}",
                headers=ORIGIN,
                params={"version": 2},
            )
            assert deleted.status_code == 200
            listing = (await client.get("/api/admin/agent/scope-rules")).json()["data"]
            assert [item["id"] for item in listing] == []
            after_delete = await client.post(
                "/api/admin/agent/scope-rules/test", headers=ORIGIN, json={"message": "早上好呀"}
            )
            assert after_delete.json()["data"]["source"] != "RUNTIME_RULE"

            reused = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "拦截闲聊问候",
                    "decision": "ALLOW",
                    "matchType": "EXACT",
                    "pattern": "项目风险概览",
                },
            )
            assert reused.status_code == 200

            async with scope_rules_database() as session:
                rows = (
                    await session.scalars(
                        select(AgentScopeRule).where(
                            AgentScopeRule.name == "拦截闲聊问候"
                        )
                    )
                ).all()
                assert len(rows) == 2
                assert rows[0].deletedAt is not None
                events = (
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.resourceId == rule_id,
                            AuditLog.module == "ADMIN_AGENT_SCOPE",
                        )
                    )
                ).all()
                assert {event.action for event in events} >= {
                    "ADMIN_SCOPE_RULE_CREATED",
                    "ADMIN_SCOPE_RULE_UPDATED",
                    "ADMIN_SCOPE_RULE_DELETED",
                }
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_scope_rule_test_endpoint_reports_builtin_and_default_sources(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, _ = await _client(scope_rules_database, identity)
        try:
            builtin_allow = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={"message": "当前有哪些高风险项目"},
            )
            assert builtin_allow.json()["data"] == {
                "decision": "ALLOW",
                "source": "BUILTIN",
                "matchedRule": None,
                "preview": False,
                "previewRuleId": None,
                "warnings": [],
            }
            builtin_block = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={"message": "帮我写一封邮件"},
            )
            assert builtin_block.json()["data"]["decision"] == "BLOCK"
            assert builtin_block.json()["data"]["source"] == "BUILTIN"
            deferred = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={"message": "那南岸呢"},
            )
            assert deferred.json()["data"] == {
                "decision": "DEFER",
                "source": "DEFAULT",
                "matchedRule": None,
                "preview": False,
                "previewRuleId": None,
                "warnings": [],
            }
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_scope_rule_endpoints_require_permission(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        client, _ = await _client(scope_rules_database, await _identity(scope_rules_database, []))
        try:
            rule_id = uuid.uuid4()
            assert (await client.get("/api/admin/agent/scope-rules")).status_code == 403
            assert (
                await client.post(
                    "/api/admin/agent/scope-rules",
                    headers=ORIGIN,
                    json={
                        "name": "越权规则",
                        "decision": "BLOCK",
                        "matchType": "PHRASE",
                        "pattern": "测试",
                    },
                )
            ).status_code == 403
            assert (
                await client.patch(
                    f"/api/admin/agent/scope-rules/{rule_id}",
                    headers=ORIGIN,
                    json={"version": 1},
                )
            ).status_code == 403
            assert (
                await client.delete(
                    f"/api/admin/agent/scope-rules/{rule_id}",
                    headers=ORIGIN,
                    params={"version": 1},
                )
            ).status_code == 403
            assert (
                await client.post(
                    "/api/admin/agent/scope-rules/test",
                    headers=ORIGIN,
                    json={"message": "项目"},
                )
            ).status_code == 403
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_scope_rule_rejects_regex_match_type_and_invalid_payloads(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, _ = await _client(scope_rules_database, identity)
        try:
            payloads = [
                {  # REGEX is not a V1 match type.
                    "name": "正则规则",
                    "decision": "BLOCK",
                    "matchType": "REGEX",
                    "pattern": ".*",
                },
                {  # priority above the 0..1000 range
                    "name": "超界规则",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "测试",
                    "priority": 1001,
                },
                {  # blank name
                    "name": "   ",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "测试",
                },
                {  # pattern beyond 200 characters
                    "name": "超长规则",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "长" * 201,
                },
            ]
            for payload in payloads:
                response = await client.post(
                    "/api/admin/agent/scope-rules", headers=ORIGIN, json=payload
                )
                assert response.status_code == 422, payload
            listing = (await client.get("/api/admin/agent/scope-rules")).json()["data"]
            assert [item["name"] for item in listing if item["name"] == "正则规则"] == []
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_migration_grants_system_admin_on_deployed_database() -> None:
    """A database deployed before 0019 gains the permission purely by upgrading."""

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL agent scope migration 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t0am_{uuid.uuid4().hex}"
    engine = create_engine(sync_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        migration_engine = create_engine(
            sync_url, connect_args={"options": f"-csearch_path={schema}"}
        )
        try:
            with migration_engine.connect() as connection:
                config = Config(ROOT / "alembic.ini")
                config.attributes["connection"] = connection
                # Simulate an already-deployed database: stop at 0018 with a
                # SYSTEM_ADMIN role present (as every deployed instance has).
                command.upgrade(config, "20260820_0018")
                connection.commit()
                connection.execute(
                    text(
                        'INSERT INTO roles (id, code, name, "defaultDataScope", "createdAt", '
                        '"updatedAt") VALUES (gen_random_uuid(), \'SYSTEM_ADMIN\', '
                        "'系统管理员', 'ALL', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
                connection.commit()
                command.upgrade(config, "head")
                connection.commit()
            with migration_engine.connect() as connection:
                grant = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM role_permissions rp "
                        "JOIN permissions p ON p.id = rp.\"permissionId\" "
                        "JOIN roles r ON r.id = rp.\"roleId\" "
                        "WHERE p.code = 'agent.scope.manage' AND r.code = 'SYSTEM_ADMIN'"
                    )
                ).scalar_one()
                revision = connection.execute(
                    text('SELECT "revision" FROM agent_scope_rule_revision WHERE id = 1')
                ).scalar_one()
        finally:
            migration_engine.dispose()
        assert grant == 1
        assert revision == 0
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_scope_rule_preview_candidate_resolves_conflict_with_live_rules(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    """A candidate is placed into the real production ordering, not tested alone."""

    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, _ = await _client(scope_rules_database, identity)
        try:
            # Live: broad BLOCK PHRASE "大足" at priority 100.
            created = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "大足白名单外拦截",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "大足",
                    "priority": 100,
                },
            )
            rule_id = created.json()["data"]["id"]
            await client.patch(
                f"/api/admin/agent/scope-rules/{rule_id}",
                headers=ORIGIN,
                json={"enabled": True, "version": 1},
            )

            # Without a candidate the live rule blocks the business question.
            plain = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={"message": "大足有哪些项目"},
            )
            plain_data = plain.json()["data"]
            assert plain_data["decision"] == "BLOCK"
            assert plain_data["source"] == "RUNTIME_RULE"
            assert plain_data["matchedRule"]["id"] == rule_id

            # Candidate ALLOW EXACT at the same priority wins by specificity.
            winner = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={
                    "message": "大足有哪些项目",
                    "candidateRule": {
                        "decision": "ALLOW",
                        "matchType": "EXACT",
                        "pattern": "大足有哪些项目",
                        "priority": 100,
                    },
                },
            )
            assert winner.status_code == 200
            winner_data = winner.json()["data"]
            assert winner_data["preview"] is True
            assert winner_data["previewRuleId"] is None
            assert winner_data["decision"] == "ALLOW"
            assert winner_data["source"] == "RUNTIME_RULE"
            assert winner_data["matchedRule"] == {
                "id": "",
                "name": "(预览规则)",
                "matchType": "EXACT",
                "decision": "ALLOW",
                "priority": 100,
            }

            # A lower-priority candidate loses to the live rule and the
            # response still names the *live* rule as the winner.
            loser = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={
                    "message": "大足有哪些项目",
                    "candidateRule": {
                        "decision": "ALLOW",
                        "matchType": "EXACT",
                        "pattern": "大足有哪些项目",
                        "priority": 10,
                    },
                },
            )
            loser_data = loser.json()["data"]
            assert loser_data["decision"] == "BLOCK"
            assert loser_data["matchedRule"]["id"] == rule_id
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_scope_rule_preview_is_read_only(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    """ruleId / candidateRule previews change nothing: no cache swap, no row
    update, no revision bump, no invalidation publish."""

    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, store = await _client(scope_rules_database, identity)
        try:
            created = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "预览只读规则",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "预览专用短语",
                },
            )
            rule_id = created.json()["data"]["id"]
            snapshot_before = store.get_snapshot()

            async with scope_rules_database() as session:
                revision_before = (
                    await session.execute(
                        text('SELECT "revision" FROM agent_scope_rule_revision WHERE id = 1')
                    )
                ).scalar_one()

            by_id = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={"message": "预览专用短语", "ruleId": rule_id},
            )
            assert by_id.json()["data"]["decision"] == "BLOCK"
            by_candidate = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={
                    "message": "预览专用短语",
                    "candidateRule": {
                        "decision": "BLOCK",
                        "matchType": "PHRASE",
                        "pattern": "预览专用短语",
                    },
                },
            )
            assert by_candidate.json()["data"]["decision"] == "BLOCK"

            # (D) the live snapshot object is untouched.
            assert store.get_snapshot() is snapshot_before
            # (E) the saved rule row is untouched.
            async with scope_rules_database() as session:
                row = (
                    await session.execute(
                        text(
                            f'SELECT "enabled", "version" FROM agent_scope_rules '
                            f"WHERE id = '{rule_id}'"
                        )
                    )
                ).one()
                # (F) the revision did not move.
                revision_after = (
                    await session.execute(
                        text('SELECT "revision" FROM agent_scope_rule_revision WHERE id = 1')
                    )
                ).scalar_one()
            assert row.enabled is False
            assert row.version == 1
            assert revision_after == revision_before

            # (G) no invalidation was published for either preview.
            published: list[int] = []

            class _PublishProbe:
                def subscribe(self, callback: object) -> None:
                    return None

                async def publish(self, revision: int) -> None:
                    published.append(revision)

                async def run(self) -> None:
                    return None

                async def aclose(self) -> None:
                    return None

            probe_store = ScopeRuleStore(scope_rules_database, _PublishProbe())
            await probe_store.refresh()
            service = AdminAgentScopeRulesService(scope_rules_database, probe_store)
            await service.test(
                ScopeRuleTestRequest(message="预览专用短语", ruleId=uuid.UUID(rule_id))
            )
            await service.test(
                ScopeRuleTestRequest(
                    message="预览专用短语",
                    candidateRule=ScopeRuleCandidateRule(
                        decision="BLOCK", matchType="PHRASE", pattern="预览专用短语"
                    ),
                )
            )
            assert published == []
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_scope_rule_test_rejects_both_preview_targets(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, _ = await _client(scope_rules_database, identity)
        try:
            both = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={
                    "message": "任意消息",
                    "ruleId": str(uuid.uuid4()),
                    "candidateRule": {
                        "decision": "BLOCK",
                        "matchType": "PHRASE",
                        "pattern": "项目",
                    },
                },
            )
            assert both.status_code == 422
            missing = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={"message": "任意消息", "ruleId": str(uuid.uuid4())},
            )
            assert missing.status_code == 404
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_broad_block_rules_carry_warnings_but_still_save(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, _ = await _client(scope_rules_database, identity)
        try:
            def codes(data: dict[str, Any]) -> list[str]:
                return [str(item["code"]) for item in data["warnings"]]

            broad = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "过宽拦截项目",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "项目",
                },
            )
            assert broad.status_code == 200  # warning never blocks the save
            broad_rule = broad.json()["data"]
            assert codes(broad_rule) == ["BROAD_BLOCK_RULE"]
            assert "项目" in broad_rule["warnings"][0]["message"]

            narrow = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "正常拦截问候",
                    "decision": "BLOCK",
                    "matchType": "PHRASE",
                    "pattern": "早上好",
                },
            )
            assert narrow.json()["data"]["warnings"] == []

            # Warnings follow updates and appear on /test previews too.
            updated = await client.patch(
                f"/api/admin/agent/scope-rules/{narrow.json()['data']['id']}",
                headers=ORIGIN,
                json={"pattern": "风险", "version": 1},
            )
            assert codes(updated.json()["data"]) == ["BROAD_BLOCK_RULE"]

            previewed = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={
                    "message": "当前风险有哪些",
                    "candidateRule": {
                        "decision": "BLOCK",
                        "matchType": "PHRASE",
                        "pattern": "风险",
                    },
                },
            )
            assert codes(previewed.json()["data"]) == ["BROAD_BLOCK_RULE"]

            # A single-character BLOCK pattern is flagged as too short.
            short = await client.post(
                "/api/admin/agent/scope-rules/test",
                headers=ORIGIN,
                json={
                    "message": "项",
                    "candidateRule": {
                        "decision": "BLOCK",
                        "matchType": "PHRASE",
                        "pattern": "项",
                    },
                },
            )
            assert codes(short.json()["data"]) == ["BROAD_BLOCK_RULE", "SHORT_BLOCK_PATTERN"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_revision_row_self_heals_after_deletion(
    scope_rules_database: async_sessionmaker[AsyncSession],
) -> None:
    """A mutation on a database whose revision row vanished recreates it."""

    async def scenario() -> None:
        identity = await _identity(scope_rules_database, ["agent.scope.manage"])
        client, store = await _client(scope_rules_database, identity)
        try:
            async with transaction(scope_rules_database) as session:
                await session.execute(text("DELETE FROM agent_scope_rule_revision"))

            created = await client.post(
                "/api/admin/agent/scope-rules",
                headers=ORIGIN,
                json={
                    "name": "自愈验证规则",
                    "decision": "ALLOW",
                    "matchType": "PHRASE",
                    "pattern": "自愈专用短语",
                },
            )
            assert created.status_code == 200
            rule_id = created.json()["data"]["id"]

            async with scope_rules_database() as session:
                revision = (
                    await session.execute(
                        text('SELECT "revision" FROM agent_scope_rule_revision WHERE id = 1')
                    )
                ).scalar_one()
            assert revision >= 1

            # The store notices the healed revision and reloads the rule.
            enabled = await client.patch(
                f"/api/admin/agent/scope-rules/{rule_id}",
                headers=ORIGIN,
                json={"enabled": True, "version": 1},
            )
            assert enabled.status_code == 200
            await store.probe()
            evaluation = evaluate_with_snapshot("自愈专用短语", store.get_snapshot())
            assert evaluation.decision is ScopeDecision.ALLOW
            assert evaluation.source is ScopeDecisionSource.RUNTIME_RULE
            assert store.get_snapshot().revision == revision + 1
        finally:
            await client.aclose()

    asyncio.run(scenario())
