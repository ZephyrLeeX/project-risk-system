"""T037 release acceptance harness.

Shared fixtures for the cross-module compatibility, authorization, security,
audit and reliability acceptance suite. The harness stands up an isolated
per-module PostgreSQL 16 schema (Alembic ``head``), seeds the approved
reference data (four roles, fifteen permissions, five departments, risk
categories/levels) plus one user per default role with real argon2 password
hashes, and composes the full production FastAPI application (all 17 routers)
bound to that schema via :func:`build_services`.

The harness is acceptance evidence only: it never edits feature-module source
or tests. Tests consume the approved public services and routers exactly as
production does.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.admin.options.api import router as admin_options_router
from risk_platform.admin.overview.api import router as overview_router
from risk_platform.admin.overview.service import OverviewDependencyFailure
from risk_platform.admin.roles.api import router as admin_roles_router
from risk_platform.admin.users.api import router as admin_users_router
from risk_platform.agent.api import router as agent_router
from risk_platform.ai_providers.api import router as ai_providers_router
from risk_platform.app import AppComposition, create_app
from risk_platform.audit.api import router as audit_router
from risk_platform.auth.api import current_identity
from risk_platform.auth.api import router as auth_router
from risk_platform.auth.schemas import AuthenticatedUser, RoleCode
from risk_platform.auth.service import SessionIdentity
from risk_platform.composition import build_services, load_cipher
from risk_platform.config import Settings
from risk_platform.dashboard.api import router as dashboard_router
from risk_platform.db import create_session_factory, transaction
from risk_platform.imports.api import router as imports_router
from risk_platform.mailbox.api import candidate_router
from risk_platform.mailbox.api import router as mailbox_router
from risk_platform.mailbox.sync_results import router as mailbox_sync_results_router
from risk_platform.projects.models import Project, ProjectRiskLevel, ProjectStatus
from risk_platform.rbac.models import DataScopeType, Role, UserRole
from risk_platform.retention.api import router as retention_router
from risk_platform.risks.api import router as risks_router
from risk_platform.risks.models import Risk, RiskCategory, RiskSourceType
from risk_platform.seed import ROLES, SeedSettings, seed_reference_data
from risk_platform.shared.crypto import SecretCipher
from risk_platform.system_config.api import router as system_config_router
from risk_platform.todos.api import router as todos_router
from risk_platform.weekly_reports.api import router as weekly_reports_router

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PASSWORD = "Acceptance_Strong1!"

# Fixed UUIDs so seeded facts and injected identities reference the same user.
SYSTEM_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000037")
RISK_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
PROJECT_MANAGER_ID = uuid.UUID("00000000-0000-0000-0000-000000000070")
VIEWER_AUDITOR_ID = uuid.UUID("00000000-0000-0000-0000-00000000007a")

ALL_ROUTERS = (
    auth_router,
    dashboard_router,
    risks_router,
    todos_router,
    weekly_reports_router,
    overview_router,
    admin_users_router,
    admin_roles_router,
    admin_options_router,
    ai_providers_router,
    audit_router,
    system_config_router,
    retention_router,
    mailbox_router,
    candidate_router,
    mailbox_sync_results_router,
    imports_router,
    agent_router,
)

# Role -> (dataScope, permission codes) mirror of seed.ROLES, for identity
# injection without a round-trip through the session table.
ROLE_PROFILES: Mapping[str, tuple[DataScopeType, tuple[str, ...]]] = {
    code: (data_scope, perms) for code, _name, _desc, data_scope, perms in ROLES
}


@dataclass(frozen=True, slots=True)
class AcceptanceSeed:
    """Stable identifiers of the seeded acceptance scenario."""

    users: Mapping[str, uuid.UUID] = field(default_factory=dict)
    projects: Mapping[str, uuid.UUID] = field(default_factory=dict)
    category_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class AcceptanceEnv:
    factory: async_sessionmaker[AsyncSession]
    seed: AcceptanceSeed
    settings: Settings
    cipher: SecretCipher
    import_root: Path


@dataclass(frozen=True, slots=True)
class AcceptanceHarness:
    """Acceptance helpers bound to one seeded environment."""

    env: AcceptanceEnv

    def build_app(
        self, *, identity: SessionIdentity | None = None, production: bool = False
    ) -> FastAPI:
        """Compose the full production app against the seeded schema.

        When ``identity`` is supplied, ``current_identity`` is overridden so a
        test can drive any role/scope without a real cookie login. When it is
        ``None`` the real cookie/session path is used (CSRF/Cookie contract).
        ``production`` selects the production environment so cookie ``Secure``
        and full origin validation apply exactly as in deployment.
        """

        env = self.env
        settings = (
            Settings(
                environment="production",
                cors_origins=env.settings.cors_origins,
                session_secret_file=env.settings.session_secret_file,
            )
            if production
            else env.settings
        )

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            async def overview_api_check() -> None:
                if not _overview_route_registered(app):
                    raise OverviewDependencyFailure("CHECK_FAILED")

            services = build_services(
                env.factory,
                settings,
                env.cipher,
                env.import_root,
                overview_api_check=overview_api_check,
            )
            for name, service in services.items():
                setattr(app.state, name, service)
            yield

        app = create_app(
            settings,
            AppComposition(routers=ALL_ROUTERS, lifespan=lifespan),
        )
        if identity is not None:
            app.dependency_overrides[current_identity] = _override(identity)
        return app

    async def client(self, app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="https://testserver"
            ) as transport,
        ):
            yield transport

    def identity_for(
        self, role: str, *, scope: DataScopeType | None = None, user_id: uuid.UUID | None = None
    ) -> SessionIdentity:
        data_scope, permissions = ROLE_PROFILES[role]
        resolved_scope = scope if scope is not None else data_scope
        uid = user_id or self.env.seed.users[role]
        return self._identity(uid, [role], list(permissions), resolved_scope)

    def full_identity(self, *, scope: DataScopeType = DataScopeType.ALL) -> SessionIdentity:
        """Every approved permission unioned (envelope/contract tests only)."""

        permissions: list[str] = []
        for _code, _name, _desc, _scope, perms in ROLES:
            permissions.extend(perms)
        unique = list(dict.fromkeys(permissions))
        return self._identity(SYSTEM_ADMIN_ID, list(ROLE_PROFILES), unique, scope)

    def _identity(
        self,
        uid: uuid.UUID,
        role_codes: list[str],
        permissions: list[str],
        scope: DataScopeType,
    ) -> SessionIdentity:
        return SessionIdentity(
            session_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            user=AuthenticatedUser(
                id=str(uid),
                username="acceptance-full",
                displayName="验收全权限",
                departmentName="技术管理部",
                roleCodes=cast(list[RoleCode], role_codes),
                permissions=permissions,
                dataScope=scope.value,
                mustChangePassword=False,
            ),
        )

    async def login(self, client: httpx2.AsyncClient, role: str) -> httpx2.Response:
        username = f"acceptance-{role.lower()}"
        return await client.post(
            "/api/auth/login",
            headers={"origin": "https://web.internal"},
            json={"username": username, "password": ACCEPTANCE_PASSWORD},
        )


def _override(identity: SessionIdentity) -> Any:
    async def provide() -> SessionIdentity:
        return identity

    return provide


def _overview_route_registered(app: FastAPI) -> bool:
    def contains(items: Any) -> bool:
        for item in items:
            if getattr(item, "path", None) == "/admin/overview":
                return True
            nested = getattr(getattr(item, "original_router", None), "routes", ())
            if contains(nested):
                return True
        return False

    return contains(app.router.routes)


async def _seed_acceptance(factory: async_sessionmaker[AsyncSession]) -> AcceptanceSeed:
    """Seed reference data, four role users, three projects and three risks."""

    hasher = PasswordHasher()
    async with transaction(factory) as session:
        await seed_reference_data(
            session,
            SeedSettings(
                username="acceptance-system_admin",
                display_name="验收系统管理员",
                password=ACCEPTANCE_PASSWORD,
                password_min_length=12,
            ),
        )
        await session.flush()
        roles = {
            code: await session.scalar(select(Role).where(Role.code == code))
            for code in ("SYSTEM_ADMIN", "RISK_ADMIN", "PROJECT_MANAGER", "VIEWER_AUDITOR")
        }
        department = await session.scalar(
            select(Department).where(Department.code == "TECH_MANAGEMENT")
        )
        category = await session.scalar(
            select(RiskCategory).where(RiskCategory.code == "COLLECTION")
        )
        assert department is not None and category is not None
        assert all(roles.values())

        # The seed created the SYSTEM_ADMIN user under a different username;
        # reuse its id so injected identities match a real row.
        admin_row = await session.scalar(
            select(User).where(User.username == "acceptance-system_admin")
        )
        assert admin_row is not None
        user_ids = {
            "SYSTEM_ADMIN": admin_row.id,
            "RISK_ADMIN": RISK_ADMIN_ID,
            "PROJECT_MANAGER": PROJECT_MANAGER_ID,
            "VIEWER_AUDITOR": VIEWER_AUDITOR_ID,
        }
        for role_code, uid in user_ids.items():
            if role_code == "SYSTEM_ADMIN":
                continue
            user = User(
                id=uid,
                username=f"acceptance-{role_code.lower()}",
                passwordHash=hasher.hash(ACCEPTANCE_PASSWORD),
                displayName=f"验收{role_code}",
                departmentId=department.id,
                status=UserStatus.ACTIVE,
                mustChangePassword=False,
            )
            session.add(user)
            await session.flush()
            data_scope, _ = ROLE_PROFILES[role_code]
            role = roles[role_code]
            assert role is not None
            session.add(UserRole(userId=user.id, roleId=role.id, dataScope=data_scope))

        owned = Project(
            name="验收-本人负责项目",
            managerId=PROJECT_MANAGER_ID,
            deliveryOwnerName="项目经理甲",
            annualPlanAmount=120,
            remainingAmount=100,
            actualCollectedAmount=20,
            departmentId=department.id,
            status=ProjectStatus.DELIVERY,
        )
        assigned = Project(
            name="验收-授权项目",
            deliveryOwnerName="交付负责人乙",
            annualPlanAmount=240,
            remainingAmount=200,
            actualCollectedAmount=40,
            departmentId=department.id,
            status=ProjectStatus.DELIVERY,
        )
        other = Project(
            name="验收-范围外项目",
            deliveryOwnerName="交付负责人丙",
            annualPlanAmount=360,
            remainingAmount=300,
            actualCollectedAmount=60,
            departmentId=department.id,
            status=ProjectStatus.DELIVERY,
        )
        session.add_all([owned, assigned, other])
        await session.flush()
        from risk_platform.rbac.models import UserProjectScope

        session.add(UserProjectScope(projectId=assigned.id, userId=PROJECT_MANAGER_ID))
        session.add(UserProjectScope(projectId=assigned.id, userId=VIEWER_AUDITOR_ID))
        for project in (owned, assigned, other):
            session.add(
                Risk(
                    projectId=project.id,
                    categoryId=category.id,
                    title=f"风险-{project.name}",
                    description=f"{project.name} 的验收风险描述",
                    level=ProjectRiskLevel.HIGH,
                    sourceType=RiskSourceType.MANUAL,
                    dedupeFingerprint=f"acceptance-{project.id}",
                    detectedAt=datetime.now(UTC),
                )
            )

        return AcceptanceSeed(
            users=user_ids,
            projects={"owned": owned.id, "assigned": assigned.id, "other": other.id},
            category_id=category.id,
            department_id=department.id,
        )


@pytest.fixture(scope="module")
def acceptance_env() -> Iterator[AcceptanceEnv]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未配置; PostgreSQL acceptance validation 未执行")
    cipher = load_cipher()
    if cipher is None:
        pytest.skip("DATA_ENCRYPTION_KEY 未配置; 加密服务不可用, acceptance validation 未执行")
    sync_url = re.sub(r"^postgresql(?:\+psycopg)?://", "postgresql+psycopg://", url)
    schema = f"t037_{uuid.uuid4().hex}"
    admin_engine = create_engine(sync_url)
    with admin_engine.begin() as connection:
        from sqlalchemy import text

        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(sync_url, connect_args={"options": f"-csearch_path={schema}"})
    with migration_engine.connect() as connection:
        config = Config(ROOT / "alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    migration_engine.dispose()

    async_url = f"{sync_url}?options=-csearch_path%3D{schema}"
    # NullPool: each session opens a fresh, loop-local psycopg connection and
    # closes it on return. Tests run one ``asyncio.run`` per case, so pooling
    # across loops would reuse/leak connections bound to a dead event loop and
    # flake under suite load. NullPool keeps the acceptance schema isolated
    # without affecting the production engine configuration.
    engine = create_async_engine(async_url, poolclass=NullPool)
    factory = create_session_factory(engine)
    seed = asyncio.run(_seed_acceptance(factory))

    key_file = Path(tempfile.mkdtemp()) / "session-key"
    key_file.write_bytes(os.urandom(32))
    settings = Settings(
        environment="test",
        cors_origins=("https://web.internal",),
        session_secret_file=key_file,
    )
    import_root = Path(tempfile.mkdtemp())

    try:
        yield AcceptanceEnv(factory, seed, settings, cipher, import_root)
    finally:
        asyncio.run(engine.dispose())
        from sqlalchemy import text

        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def acceptance(acceptance_env: AcceptanceEnv) -> AcceptanceHarness:
    return AcceptanceHarness(acceptance_env)


# Re-exported for test modules that build identities directly.
__all__ = [
    "ACCEPTANCE_PASSWORD",
    "PROJECT_MANAGER_ID",
    "RISK_ADMIN_ID",
    "SYSTEM_ADMIN_ID",
    "VIEWER_AUDITOR_ID",
    "AcceptanceHarness",
    "acceptance",
    "acceptance_env",
]
