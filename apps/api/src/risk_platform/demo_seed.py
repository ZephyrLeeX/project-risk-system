"""Create the repeatable, synthetic INTERNAL_MVP business demo dataset.

This is deliberately a one-shot command used by the deployment wrapper.  It
uses the production SQLAlchemy models and PostgreSQL transaction boundary, but
never touches reference rows created by ``risk-platform-seed`` or deletes
existing business data.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.db import (
    create_database_engine,
    create_session_factory,
    database_url,
    dispose_database_engine,
    transaction,
)
from risk_platform.projects.models import (
    Project,
    ProjectAlias,
    ProjectStatus,
)
from risk_platform.projects.models import (
    ProjectRiskLevel as ProjectCollectionRiskLevel,
)
from risk_platform.rbac.models import ProjectScopeSource, Role, UserProjectScope, UserRole
from risk_platform.risks.models import (
    ProjectRiskLevel as RiskLevel,
)
from risk_platform.risks.models import (
    Risk,
    RiskCategory,
    RiskSourceType,
    RiskStatus,
)
from risk_platform.todos.models import (
    ActionItem,
    ActionItemSourceType,
    ActionItemStatus,
    ActionItemUrgency,
)

DEMO_NAMESPACE: Final = UUID("9b2b3e9d-80be-4ab4-8a51-cd4f4e5ed0e0")
DEMO_MARKER: Final = "WSLDEMO"
DEMO_NOW: Final = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
SYNTHETIC_PASSWORD: Final = "WSLDEMO-Demo-Password-2026!"

CANONICAL_PROJECTS: Final[tuple[str, ...]] = (
    "WSLDEMO-ERP 系统升级",
    "WSLDEMO-供应链平台上线",
    "WSLDEMO-客户数据迁移",
    "WSLDEMO-移动端重构",
    "WSLDEMO-财务系统集成",
    "WSLDEMO-AI 风险识别试点",
    "WSLDEMO-海外交付项目",
    "WSLDEMO-内部安全整改",
)


class DemoRiskStage(StrEnum):
    OPEN = "open"
    MONITORING = "monitoring"
    MITIGATED = "mitigated"
    CLOSED = "closed"


def stable_id(kind: str, key: str) -> UUID:
    return uuid5(DEMO_NAMESPACE, f"{kind}:{key}")


def normalized_alias(value: str) -> str:
    return "".join(ch.casefold() for ch in value if ch.isalnum())


def _risk_fingerprint(key: str) -> str:
    return hashlib.sha256(f"{DEMO_MARKER}:risk:{key}".encode()).hexdigest()


async def _get_or_create_user(
    session: AsyncSession,
    *,
    username: str,
    display_name: str,
    department_id: UUID,
    password_hash: str,
) -> User:
    user = await session.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(
            id=stable_id("user", username),
            username=username,
            displayName=display_name,
            passwordHash=password_hash,
            departmentId=department_id,
            status=UserStatus.ACTIVE,
            mustChangePassword=False,
        )
        session.add(user)
    else:
        user.displayName = display_name
        user.departmentId = department_id
        user.status = UserStatus.ACTIVE
    await session.flush()
    return user


async def _upsert_project(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    status: ProjectStatus,
    department_id: UUID,
    manager_id: UUID,
    index: int,
) -> Project:
    import_key = f"{DEMO_MARKER}-PROJECT-{key}"
    project = await session.scalar(select(Project).where(Project.importKey == import_key))
    if project is None:
        project = Project(id=stable_id("project", key), importKey=import_key)
        session.add(project)
    project.externalCode = f"{DEMO_MARKER}-{index:02d}"
    project.name = name
    project.alias = name
    project.status = status
    project.departmentId = department_id
    project.managerId = manager_id
    project.deliveryOwnerName = "WSLDEMO 合成交付组"
    project.annualPlanAmount = Decimal(1_000_000 + index * 125_000)
    project.actualCollectedAmount = Decimal(320_000 + index * 31_000)
    project.remainingAmount = Decimal(680_000 + index * 94_000)
    project.collectionRiskLevel = (
        ProjectCollectionRiskLevel.HIGH if index % 4 == 0 else ProjectCollectionRiskLevel.MEDIUM
    )
    project.collectionProgress = "WSLDEMO synthetic data, INTERNAL_MVP demo only."
    project.lastImportedAt = DEMO_NOW
    await session.flush()
    alias = await session.scalar(
        select(ProjectAlias).where(ProjectAlias.normalizedAlias == normalized_alias(name))
    )
    if alias is None:
        session.add(
            ProjectAlias(
                id=stable_id("alias", key),
                projectId=project.id,
                alias=name,
                normalizedAlias=normalized_alias(name),
                source=DEMO_MARKER,
                note="WSLDEMO synthetic canonical mail-matching alias",
                isActive=True,
            )
        )
    else:
        alias.projectId = project.id
        alias.alias = name
        alias.source = DEMO_MARKER
        alias.note = "WSLDEMO synthetic canonical mail-matching alias"
        alias.isActive = True
    await session.flush()
    return project


async def _upsert_risk(
    session: AsyncSession,
    *,
    key: str,
    project_id: UUID,
    category_id: UUID,
    reporter_id: UUID,
    stage: DemoRiskStage,
    level: RiskLevel,
    index: int,
) -> Risk:
    fingerprint = _risk_fingerprint(key)
    risk = await session.scalar(select(Risk).where(Risk.dedupeFingerprint == fingerprint))
    status = RiskStatus.RESOLVED if stage is DemoRiskStage.CLOSED else RiskStatus.ACTIVE
    if risk is None:
        risk = Risk(id=stable_id("risk", key), dedupeFingerprint=fingerprint)
        session.add(risk)
    risk.projectId = project_id
    risk.categoryId = category_id
    risk.title = f"[WSLDEMO][{stage.value}] 合成风险 {index:02d}"
    risk.description = f"WSLDEMO synthetic demo risk ({stage.value}); 不含真实客户、个人或凭据。"
    risk.evidence = "WSLDEMO synthetic evidence"
    risk.level = level
    risk.status = status
    risk.sourceType = RiskSourceType.MANUAL
    risk.reporterUserId = reporter_id
    risk.reporterNameSource = "WSLDEMO 合成用户"
    risk.weekCode = "2026-W33"
    risk.suggestion = f"WSLDEMO demo action: track the {stage.value} scenario and next step."
    risk.detectedAt = DEMO_NOW.replace(day=max(1, 16 - index % 14))
    risk.resolvedAt = DEMO_NOW if status is RiskStatus.RESOLVED else None
    risk.resolvedById = reporter_id if status is RiskStatus.RESOLVED else None
    risk.resolutionReason = (
        "WSLDEMO synthetic demo closed" if status is RiskStatus.RESOLVED else None
    )
    await session.flush()
    return risk


async def _upsert_todo(
    session: AsyncSession,
    *,
    key: str,
    project_id: UUID,
    risk_id: UUID | None,
    assignee_id: UUID,
    created_by_id: UUID,
    status: ActionItemStatus,
    index: int,
) -> None:
    todo = await session.get(ActionItem, stable_id("todo", key))
    if todo is None:
        todo = ActionItem(id=stable_id("todo", key))
        session.add(todo)
    todo.riskId = risk_id
    todo.projectId = project_id
    todo.title = f"[WSLDEMO] 演示行动项 {index:02d}"
    todo.description = "WSLDEMO synthetic demo todo; no real business information."
    todo.urgency = ActionItemUrgency.HIGH if index % 4 == 0 else ActionItemUrgency.NORMAL
    todo.status = status
    todo.sourceType = (
        ActionItemSourceType.RISK_SUGGESTION if risk_id is not None else ActionItemSourceType.MANUAL
    )
    todo.assigneeUserId = assignee_id
    todo.assigneeNameSource = "WSLDEMO 合成用户"
    todo.dueDate = DEMO_NOW.date() + timedelta(days=index - 20)
    todo.createdById = created_by_id
    todo.completedById = assignee_id if status is ActionItemStatus.COMPLETED else None
    todo.completedAt = DEMO_NOW if status is ActionItemStatus.COMPLETED else None
    todo.completionNote = (
        "WSLDEMO synthetic completed" if status is ActionItemStatus.COMPLETED else None
    )
    await session.flush()


async def seed_demo_data(session: AsyncSession) -> tuple[int, int, int, int]:
    departments = {
        row.code: row
        for row in (
            await session.scalars(select(Department).where(Department.enabled.is_(True)))
        ).all()
    }
    roles = {row.code: row for row in (await session.scalars(select(Role))).all()}
    required_departments = (
        "TECH_MANAGEMENT",
        "RISK_MANAGEMENT",
        "PROJECT_DELIVERY_1",
        "PROJECT_DELIVERY_2",
        "INTERNAL_AUDIT",
    )
    required_roles = ("RISK_ADMIN", "PROJECT_MANAGER", "VIEWER_AUDITOR")
    if any(code not in departments for code in required_departments) or any(
        code not in roles for code in required_roles
    ):
        raise RuntimeError("reference seed is incomplete; run deploy.sh --seed first")

    password_hash = PasswordHasher(type=Type.ID).hash(SYNTHETIC_PASSWORD)
    user_specs = (
        ("pm01", "项目经理一号", "PROJECT_DELIVERY_1", "PROJECT_MANAGER"),
        ("pm02", "项目经理二号", "PROJECT_DELIVERY_2", "PROJECT_MANAGER"),
        ("risk01", "风险管理员一号", "RISK_MANAGEMENT", "RISK_ADMIN"),
        ("risk02", "风险管理员二号", "RISK_MANAGEMENT", "RISK_ADMIN"),
        ("audit01", "审计员一号", "INTERNAL_AUDIT", "VIEWER_AUDITOR"),
        ("audit02", "审计员二号", "INTERNAL_AUDIT", "VIEWER_AUDITOR"),
        ("tech01", "技术负责人一号", "TECH_MANAGEMENT", "PROJECT_MANAGER"),
        ("tech02", "技术负责人二号", "TECH_MANAGEMENT", "PROJECT_MANAGER"),
        ("delivery01", "交付负责人一号", "PROJECT_DELIVERY_1", "PROJECT_MANAGER"),
        ("delivery02", "交付负责人二号", "PROJECT_DELIVERY_2", "PROJECT_MANAGER"),
    )
    users: dict[str, User] = {}
    for key, display_name, department_code, role_code in user_specs:
        user = await _get_or_create_user(
            session,
            username=f"wsldemo_{key}",
            display_name=f"WSLDEMO {display_name}",
            department_id=departments[department_code].id,
            password_hash=password_hash,
        )
        users[key] = user
        role = roles[role_code]
        user_role = await session.scalar(
            select(UserRole).where(UserRole.userId == user.id, UserRole.roleId == role.id)
        )
        if user_role is None:
            session.add(UserRole(userId=user.id, roleId=role.id, dataScope=role.defaultDataScope))
        else:
            user_role.dataScope = role.defaultDataScope

    project_specs = [
        *CANONICAL_PROJECTS,
        *[
            "WSLDEMO-数据中心迁移",
            "WSLDEMO-采购流程优化",
            "WSLDEMO-统一身份治理",
            "WSLDEMO-客户服务门户",
        ],
    ]
    managers = (users["pm01"], users["pm02"], users["delivery01"], users["delivery02"])
    projects: list[Project] = []
    for index, name in enumerate(project_specs, start=1):
        if index <= len(CANONICAL_PROJECTS):
            status = ProjectStatus.COMPLETED if index % 2 == 0 else ProjectStatus.DELIVERY
        else:
            status = (
                ProjectStatus.ARCHIVED
                if index == len(CANONICAL_PROJECTS) + 2
                else ProjectStatus.DELIVERY
            )
        projects.append(
            await _upsert_project(
                session,
                key=f"{index:02d}",
                name=name,
                status=status,
                department_id=departments[
                    required_departments[(index - 1) % len(required_departments)]
                ].id,
                manager_id=managers[index % len(managers)].id,
                index=index,
            )
        )

    for viewer_key in ("audit01", "audit02"):
        for project in projects[: len(CANONICAL_PROJECTS)]:
            scope = await session.scalar(
                select(UserProjectScope).where(
                    UserProjectScope.userId == users[viewer_key].id,
                    UserProjectScope.projectId == project.id,
                )
            )
            if scope is None:
                session.add(
                    UserProjectScope(
                        userId=users[viewer_key].id,
                        projectId=project.id,
                        assignedBy=users["risk01"].id,
                        scopeSource=ProjectScopeSource.ADMIN,
                    )
                )

    categories = (
        await session.scalars(
            select(RiskCategory)
            .where(RiskCategory.isActive.is_(True))
            .order_by(RiskCategory.sortOrder)
        )
    ).all()
    if not categories:
        raise RuntimeError(
            "reference seed has no active risk categories; run deploy.sh --seed first"
        )
    stages = tuple(DemoRiskStage)
    levels = (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW)
    risks: list[Risk] = []
    for index in range(1, 41):
        project = projects[(index - 1) % len(projects)]
        risk = await _upsert_risk(
            session,
            key=f"{index:02d}",
            project_id=project.id,
            category_id=categories[(index - 1) % len(categories)].id,
            reporter_id=managers[(index - 1) % len(managers)].id,
            stage=stages[(index - 1) % len(stages)],
            level=levels[(index - 1) % len(levels)],
            index=index,
        )
        risks.append(risk)

    todo_statuses = (
        ActionItemStatus.PENDING,
        ActionItemStatus.IN_PROGRESS,
        ActionItemStatus.COMPLETED,
    )
    for index in range(1, 73):
        risk_item: Risk | None = risks[(index - 1) % len(risks)] if index <= len(risks) else None
        project = projects[(index - 1) % len(projects)]
        await _upsert_todo(
            session,
            key=f"{index:02d}",
            project_id=project.id,
            risk_id=risk_item.id if risk_item is not None else None,
            assignee_id=managers[(index - 1) % len(managers)].id,
            created_by_id=users["risk01"].id,
            status=todo_statuses[(index - 1) % len(todo_statuses)],
            index=index,
        )
    await session.flush()
    return len(users), len(projects), len(risks), 72


async def _run() -> tuple[int, int, int, int]:
    engine = create_database_engine(database_url())
    factory = create_session_factory(engine)
    try:
        async with transaction(factory) as session:
            return await seed_demo_data(session)
    finally:
        await dispose_database_engine(engine)


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print(
            "demo seed 不接受参数; 请通过 seed-demo-data.sh --confirm-demo-data 执行",
            file=sys.stderr,
        )
        raise SystemExit(2)
    counts = asyncio.run(_run())
    print(
        f"Demo seed completed: users={counts[0]} projects={counts[1]} "
        f"risks={counts[2]} todos={counts[3]}"
    )


if __name__ == "__main__":
    main()
