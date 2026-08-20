"""Repeatable, transaction-owned PostgreSQL Seed for approved reference data."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.db import (
    create_database_engine,
    create_session_factory,
    database_url,
    dispose_database_engine,
    transaction,
)
from risk_platform.model_types import JSONValue
from risk_platform.models import Base
from risk_platform.rbac.models import (
    DataScopeType,
    Permission,
    Role,
    RolePermission,
    UserRole,
)
from risk_platform.risks.models import RiskCategory
from risk_platform.system_config.models import ProjectRiskLevel, RiskLevelRule


class SeedConfigurationError(RuntimeError):
    """Safe configuration error that never contains credential values."""


@dataclass(frozen=True)
class SeedSettings:
    username: str
    display_name: str
    password: str
    password_min_length: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SeedSettings:
        source = os.environ if environ is None else environ
        username = source.get("INITIAL_ADMIN_USERNAME", "admin").strip().casefold()
        display_name = source.get("INITIAL_ADMIN_DISPLAY_NAME", "系统管理员").strip()
        password = source.get("INITIAL_ADMIN_PASSWORD")
        try:
            minimum = int(source.get("PASSWORD_MIN_LENGTH", "12"))
        except ValueError:
            raise SeedConfigurationError("PASSWORD_MIN_LENGTH 必须是整数") from None
        if not username or len(username) > 64 or not re.fullmatch(r"[a-z0-9._-]+", username):
            raise SeedConfigurationError("INITIAL_ADMIN_USERNAME 格式无效")
        if not display_name or len(display_name) > 128:
            raise SeedConfigurationError("INITIAL_ADMIN_DISPLAY_NAME 格式无效")
        if password is None:
            raise SeedConfigurationError("缺少 INITIAL_ADMIN_PASSWORD, 拒绝创建默认密码")
        if minimum < 12 or minimum > 128:
            raise SeedConfigurationError("PASSWORD_MIN_LENGTH 必须在 12 到 128 之间")
        if _password_policy_violations(password, username=username, minimum=minimum):
            raise SeedConfigurationError("INITIAL_ADMIN_PASSWORD 不符合密码策略")
        return cls(username, display_name, password, minimum)


def _password_policy_violations(password: str, *, username: str, minimum: int) -> bool:
    return any(
        (
            len(password) < minimum,
            re.search(r"[a-z]", password) is None,
            re.search(r"[A-Z]", password) is None,
            re.search(r"\d", password) is None,
            re.search(r"[^A-Za-z0-9]", password) is None,
            username in password.casefold(),
        )
    )


PERMISSIONS: Final = (
    ("dashboard.view", "查看风险看板", "DASHBOARD"),
    ("agent.use", "使用 Agent 智能对话", "AGENT"),
    ("agent.scope.manage", "管理 Agent 范围规则", "AGENT"),
    ("risk.report", "上报项目风险", "RISK"),
    ("risk.resolve", "处理与解除项目风险", "RISK"),
    ("risk.manage_all", "管理全部项目风险", "RISK"),
    ("mailbox.manage_self", "配置个人邮箱", "MAILBOX"),
    ("mailbox.sync_self", "同步个人邮箱", "MAILBOX"),
    ("admin.user.manage", "管理用户", "ADMIN"),
    ("admin.role.manage", "管理角色权限", "ADMIN"),
    ("admin.scope.manage", "管理项目数据范围", "ADMIN"),
    ("admin.ai.manage", "管理 API Key", "ADMIN"),
    ("admin.import.manage", "管理项目数据导入", "ADMIN"),
    ("admin.config.manage", "管理系统配置", "ADMIN"),
    ("admin.audit.view", "查看审计日志", "ADMIN"),
    ("admin.audit.export", "导出审计日志", "ADMIN"),
)

ROLES: Final = (
    (
        "SYSTEM_ADMIN",
        "系统管理员",
        "负责用户、角色、权限、项目范围、API Key、导入、配置和审计。",
        DataScopeType.ALL,
        (
            "dashboard.view",
            "admin.user.manage",
            "admin.role.manage",
            "admin.scope.manage",
            "admin.ai.manage",
            "admin.import.manage",
            "admin.config.manage",
            "admin.audit.view",
            "admin.audit.export",
            "agent.scope.manage",
        ),
    ),
    (
        "RISK_ADMIN",
        "风险管理员",
        "负责全部项目风险审核、治理，以及个人邮箱配置与同步。",
        DataScopeType.ALL,
        (
            "dashboard.view",
            "agent.use",
            "risk.report",
            "risk.resolve",
            "risk.manage_all",
            "mailbox.manage_self",
            "mailbox.sync_self",
        ),
    ),
    (
        "PROJECT_MANAGER",
        "项目经理",
        "查看、上报、处理本人负责或被授权项目的风险。",
        DataScopeType.OWNED_OR_ASSIGNED,
        ("dashboard.view", "agent.use", "risk.report", "risk.resolve"),
    ),
    (
        "VIEWER_AUDITOR",
        "查看/审计员",
        "只读查看被授权项目的风险、回款、周报和审计信息。",
        DataScopeType.ASSIGNED,
        ("dashboard.view", "agent.use", "admin.audit.view"),
    ),
)

RISK_CATEGORIES: Final[tuple[tuple[str, str, list[str], int], ...]] = (
    ("COLLECTION", "回款风险", ["回款", "应收", "质保款", "验收款"], 10),
    ("LITIGATION", "发函诉讼风险", ["发函", "诉讼", "法务", "律师函"], 20),
    ("SUPPLIER", "供应商风险", ["供应商", "采购", "核减"], 30),
    ("CUSTOMER", "客户层面风险", ["客户", "甲方", "业主"], 40),
    ("COST", "成本风险", ["成本", "预算", "超支"], 50),
    ("ACCEPTANCE_DELAY", "验收延期风险", ["验收", "延期", "拖期"], 60),
    ("OUT_OF_SCOPE", "超出合同需求", ["合同外", "超范围", "新增需求"], 70),
    ("OTHER", "其他风险", [], 999),
)

RISK_LEVELS: Final = (
    (
        ProjectRiskLevel.HIGH,
        "高风险",
        "#EF5555",
        "重大回款逾期、诉讼或关键交付受阻，需要管理层立即决策。",
        ["重大逾期", "诉讼", "关键受阻"],
        10,
    ),
    (
        ProjectRiskLevel.MEDIUM,
        "中风险",
        "#F0A019",
        "存在明确影响，需持续跟踪并制定措施。",
        ["延期", "投诉", "审计"],
        20,
    ),
    (
        ProjectRiskLevel.LOW,
        "低风险",
        "#21A66D",
        "影响可控，按计划观察和推进。",
        ["关注", "观察", "提示"],
        30,
    ),
)

DEPARTMENTS: Final = (
    ("TECH_MANAGEMENT", "技术管理部"),
    ("RISK_MANAGEMENT", "风险管理组"),
    ("PROJECT_DELIVERY_1", "项目交付一部"),
    ("PROJECT_DELIVERY_2", "项目交付二部"),
    ("INTERNAL_AUDIT", "内控审计部"),
)


async def _by_code[ModelWithCode: Base](
    session: AsyncSession, model: type[ModelWithCode], code: str
) -> ModelWithCode | None:
    return cast(
        ModelWithCode | None,
        await session.scalar(select(model).where(model.__table__.c.code == code)),
    )


async def seed_reference_data(session: AsyncSession, settings: SeedSettings) -> None:
    permission_by_code: dict[str, Permission] = {}
    for code, name, module in PERMISSIONS:
        permission = await _by_code(session, Permission, code)
        if permission is None:
            permission = Permission(code=code, name=name, module=module)
            session.add(permission)
        assert isinstance(permission, Permission)
        permission.name = name
        permission.module = module
        permission.description = f"{name}的系统权限点"
        permission_by_code[code] = permission

    roles: dict[str, Role] = {}
    for code, name, description, data_scope, permission_codes in ROLES:
        role = await _by_code(session, Role, code)
        if role is None:
            role = Role(code=code, name=name, defaultDataScope=data_scope)
            session.add(role)
        assert isinstance(role, Role)
        role.name = name
        role.description = description
        role.isSystem = True
        role.enabled = True
        role.defaultDataScope = data_scope
        roles[code] = role
        await session.flush()
        await session.execute(delete(RolePermission).where(RolePermission.roleId == role.id))
        for permission_code in permission_codes:
            session.add(
                RolePermission(
                    roleId=role.id,
                    permissionId=permission_by_code[permission_code].id,
                )
            )

    for code, name, keywords, sort_order in RISK_CATEGORIES:
        category = await _by_code(session, RiskCategory, code)
        if category is None:
            category = RiskCategory(code=code, name=name)
            session.add(category)
        assert isinstance(category, RiskCategory)
        category.name = name
        category.keywords = cast(JSONValue, list(keywords))
        category.sortOrder = sort_order
        category.isActive = True

    for level, display_name, color, criteria, keywords, sort_order in RISK_LEVELS:
        rule = await session.scalar(select(RiskLevelRule).where(RiskLevelRule.level == level))
        if rule is None:
            rule = RiskLevelRule(
                level=level,
                displayName=display_name,
                colorToken=color,
                criteria=criteria,
            )
            session.add(rule)
        rule.displayName = display_name
        rule.colorToken = color
        rule.criteria = criteria
        rule.keywords = cast(JSONValue, list(keywords))
        rule.sortOrder = sort_order
        rule.isActive = True

    departments: dict[str, Department] = {}
    for index, (code, name) in enumerate(DEPARTMENTS, start=1):
        department = await _by_code(session, Department, code)
        if department is None:
            department = Department(code=code, name=name)
            session.add(department)
        assert isinstance(department, Department)
        department.name = name
        department.enabled = True
        department.sortOrder = index * 10
        departments[code] = department

    await session.flush()
    administrator = await session.scalar(select(User).where(User.username == settings.username))
    if administrator is None:
        password_hash = PasswordHasher(type=Type.ID).hash(settings.password)
        administrator = User(
            username=settings.username,
            displayName=settings.display_name,
            passwordHash=password_hash,
            departmentId=departments["TECH_MANAGEMENT"].id,
            status=UserStatus.ACTIVE,
            mustChangePassword=True,
        )
        session.add(administrator)
    else:
        administrator.displayName = settings.display_name
        administrator.departmentId = departments["TECH_MANAGEMENT"].id
    await session.flush()

    system_admin = roles["SYSTEM_ADMIN"]
    user_role = await session.scalar(
        select(UserRole).where(
            UserRole.userId == administrator.id,
            UserRole.roleId == system_admin.id,
        )
    )
    if user_role is None:
        session.add(
            UserRole(
                userId=administrator.id,
                roleId=system_admin.id,
                dataScope=DataScopeType.ALL,
            )
        )
    else:
        user_role.dataScope = DataScopeType.ALL


async def _run(settings: SeedSettings) -> None:
    engine = create_database_engine(database_url())
    factory = create_session_factory(engine)
    try:
        async with transaction(factory) as session:
            await seed_reference_data(session, settings)
    finally:
        await dispose_database_engine(engine)


def main(argv: Sequence[str] | None = None) -> None:
    try:
        arguments = tuple(sys.argv[1:] if argv is None else argv)
        if arguments:
            raise SeedConfigurationError("Seed 不接受命令行参数; credential 只能通过环境注入")
        settings = SeedSettings.from_env()
        asyncio.run(_run(settings))
    except SeedConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    print(
        "Seed completed: 16 permissions, 4 roles, 8 risk categories, "
        "3 risk levels, 5 departments, 1 initial administrator."
    )


if __name__ == "__main__":
    main()
