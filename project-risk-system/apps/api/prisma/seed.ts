import {
  DataScopeType,
  PrismaClient,
  UserStatus,
} from "@prisma/client";
import argon2 = require("argon2");

import { getPasswordPolicyViolations } from "../src/auth/password-policy";

const prisma = new PrismaClient();

const permissions = [
  ["dashboard.view", "查看风险看板", "DASHBOARD"],
  ["agent.use", "使用 Agent 智能对话", "AGENT"],
  ["risk.report", "上报项目风险", "RISK"],
  ["risk.resolve", "处理与解除项目风险", "RISK"],
  ["risk.manage_all", "管理全部项目风险", "RISK"],
  ["mailbox.manage_self", "配置个人邮箱", "MAILBOX"],
  ["mailbox.sync_self", "同步个人邮箱", "MAILBOX"],
  ["admin.user.manage", "管理用户", "ADMIN"],
  ["admin.role.manage", "管理角色权限", "ADMIN"],
  ["admin.scope.manage", "管理项目数据范围", "ADMIN"],
  ["admin.ai.manage", "管理 API Key", "ADMIN"],
  ["admin.import.manage", "管理项目数据导入", "ADMIN"],
  ["admin.config.manage", "管理系统配置", "ADMIN"],
  ["admin.audit.view", "查看审计日志", "ADMIN"],
  ["admin.audit.export", "导出审计日志", "ADMIN"],
] as const;

const roles = [
  {
    code: "SYSTEM_ADMIN",
    name: "系统管理员",
    description: "负责用户、角色、权限、项目范围、API Key、导入、配置和审计。",
    defaultDataScope: DataScopeType.ALL,
    permissions: [
      "dashboard.view",
      "admin.user.manage",
      "admin.role.manage",
      "admin.scope.manage",
      "admin.ai.manage",
      "admin.import.manage",
      "admin.config.manage",
      "admin.audit.view",
      "admin.audit.export",
    ],
  },
  {
    code: "RISK_ADMIN",
    name: "风险管理员",
    description: "负责全部项目风险审核、治理，以及个人邮箱配置与同步。",
    defaultDataScope: DataScopeType.ALL,
    permissions: [
      "dashboard.view",
      "agent.use",
      "risk.report",
      "risk.resolve",
      "risk.manage_all",
      "mailbox.manage_self",
      "mailbox.sync_self",
    ],
  },
  {
    code: "PROJECT_MANAGER",
    name: "项目经理",
    description: "查看、上报、处理本人负责或被授权项目的风险。",
    defaultDataScope: DataScopeType.OWNED_OR_ASSIGNED,
    permissions: [
      "dashboard.view",
      "agent.use",
      "risk.report",
      "risk.resolve",
    ],
  },
  {
    code: "VIEWER_AUDITOR",
    name: "查看/审计员",
    description: "只读查看被授权项目的风险、回款、周报和审计信息。",
    defaultDataScope: DataScopeType.ASSIGNED,
    permissions: ["dashboard.view", "agent.use", "admin.audit.view"],
  },
] as const;

const riskCategories = [
  {
    code: "COLLECTION",
    name: "回款风险",
    keywords: ["回款", "应收", "质保款", "验收款"],
    sortOrder: 10,
  },
  {
    code: "LITIGATION",
    name: "发函诉讼风险",
    keywords: ["发函", "诉讼", "法务", "律师函"],
    sortOrder: 20,
  },
  {
    code: "SUPPLIER",
    name: "供应商风险",
    keywords: ["供应商", "采购", "核减"],
    sortOrder: 30,
  },
  {
    code: "CUSTOMER",
    name: "客户层面风险",
    keywords: ["客户", "甲方", "业主"],
    sortOrder: 40,
  },
  {
    code: "COST",
    name: "成本风险",
    keywords: ["成本", "预算", "超支"],
    sortOrder: 50,
  },
  {
    code: "ACCEPTANCE_DELAY",
    name: "验收延期风险",
    keywords: ["验收", "延期", "拖期"],
    sortOrder: 60,
  },
  {
    code: "OUT_OF_SCOPE",
    name: "超出合同需求",
    keywords: ["合同外", "超范围", "新增需求"],
    sortOrder: 70,
  },
  {
    code: "OTHER",
    name: "其他风险",
    keywords: [],
    sortOrder: 999,
  },
] as const;

async function seed(): Promise<void> {
  const initialUsername = (
    process.env.INITIAL_ADMIN_USERNAME ?? "admin"
  )
    .trim()
    .toLocaleLowerCase();
  const initialDisplayName =
    process.env.INITIAL_ADMIN_DISPLAY_NAME?.trim() || "系统管理员";
  const initialPassword = process.env.INITIAL_ADMIN_PASSWORD;
  const passwordMinLength = Number(process.env.PASSWORD_MIN_LENGTH ?? 12);

  if (!initialPassword) {
    throw new Error(
      "缺少 INITIAL_ADMIN_PASSWORD，拒绝创建带有硬编码默认密码的管理员账号。",
    );
  }

  const violations = getPasswordPolicyViolations(initialPassword, {
    minLength: passwordMinLength,
    username: initialUsername,
  });
  if (violations.length > 0) {
    throw new Error(`INITIAL_ADMIN_PASSWORD 不符合策略：${violations.join("；")}`);
  }

  for (const [code, name, module] of permissions) {
    await prisma.permission.upsert({
      where: { code },
      create: {
        code,
        name,
        module,
        description: `${name}的系统权限点`,
      },
      update: {
        name,
        module,
        description: `${name}的系统权限点`,
      },
    });
  }

  for (const roleDefinition of roles) {
    const role = await prisma.role.upsert({
      where: { code: roleDefinition.code },
      create: {
        code: roleDefinition.code,
        name: roleDefinition.name,
        description: roleDefinition.description,
        isSystem: true,
        enabled: true,
        defaultDataScope: roleDefinition.defaultDataScope,
      },
      update: {
        name: roleDefinition.name,
        description: roleDefinition.description,
        isSystem: true,
        enabled: true,
        defaultDataScope: roleDefinition.defaultDataScope,
      },
    });

    const rolePermissions = await prisma.permission.findMany({
      where: { code: { in: [...roleDefinition.permissions] } },
      select: { id: true },
    });

    await prisma.$transaction([
      prisma.rolePermission.deleteMany({
        where: { roleId: role.id },
      }),
      prisma.rolePermission.createMany({
        data: rolePermissions.map(({ id }) => ({
          roleId: role.id,
          permissionId: id,
        })),
      }),
    ]);
  }

  for (const category of riskCategories) {
    await prisma.riskCategory.upsert({
      where: { code: category.code },
      create: {
        code: category.code,
        name: category.name,
        keywords: [...category.keywords],
        sortOrder: category.sortOrder,
        isActive: true,
      },
      update: {
        name: category.name,
        keywords: [...category.keywords],
        sortOrder: category.sortOrder,
        isActive: true,
      },
    });
  }

  const departmentDefinitions = [
    ["TECH_MANAGEMENT", "技术管理部"],
    ["RISK_MANAGEMENT", "风险管理组"],
    ["PROJECT_DELIVERY_1", "项目交付一部"],
    ["PROJECT_DELIVERY_2", "项目交付二部"],
    ["INTERNAL_AUDIT", "内控审计部"],
  ] as const;
  const seededDepartments = [];
  for (const [index, [code, name]] of departmentDefinitions.entries()) {
    seededDepartments.push(
      await prisma.department.upsert({
        where: { code },
        create: {
          code,
          name,
          enabled: true,
          sortOrder: (index + 1) * 10,
        },
        update: {
          name,
          enabled: true,
          sortOrder: (index + 1) * 10,
        },
      }),
    );
  }
  const department = seededDepartments[0]!;
  const passwordHash = await argon2.hash(initialPassword, {
    type: argon2.argon2id,
  });
  const administrator = await prisma.user.upsert({
    where: { username: initialUsername },
    create: {
      username: initialUsername,
      displayName: initialDisplayName,
      passwordHash,
      departmentId: department.id,
      status: UserStatus.ACTIVE,
      mustChangePassword: true,
    },
    update: {
      displayName: initialDisplayName,
      departmentId: department.id,
    },
  });
  const systemAdminRole = await prisma.role.findUniqueOrThrow({
    where: { code: "SYSTEM_ADMIN" },
  });

  await prisma.userRole.upsert({
    where: {
      userId_roleId: {
        userId: administrator.id,
        roleId: systemAdminRole.id,
      },
    },
    create: {
      userId: administrator.id,
      roleId: systemAdminRole.id,
      dataScope: systemAdminRole.defaultDataScope,
    },
    update: {
      dataScope: systemAdminRole.defaultDataScope,
    },
  });

  console.info(
    `Seed completed: ${permissions.length} permissions, ${roles.length} roles, ${riskCategories.length} risk categories, administrator "${initialUsername}".`,
  );
}

seed()
  .catch((error: unknown) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
