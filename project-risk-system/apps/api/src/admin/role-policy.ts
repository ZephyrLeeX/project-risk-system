import { BadRequestException } from "@nestjs/common";
import { DataScopeType } from "@prisma/client";

const SYSTEM_ADMIN_REQUIRED = new Set([
  "dashboard.view",
  "admin.user.manage",
  "admin.role.manage",
  "admin.scope.manage",
  "admin.ai.manage",
  "admin.import.manage",
  "admin.config.manage",
  "admin.audit.view",
]);
const SYSTEM_ADMIN_ONLY = new Set([
  "admin.user.manage",
  "admin.role.manage",
  "admin.scope.manage",
  "admin.ai.manage",
  "admin.import.manage",
  "admin.config.manage",
]);
const RISK_ADMIN_ONLY = new Set([
  "mailbox.manage_self",
  "mailbox.sync_self",
]);

export function validateRolePolicy(
  roleCode: string,
  permissionCodes: string[],
  dataScope: DataScopeType,
): void {
  const permissions = new Set(permissionCodes);

  if (
    roleCode === "SYSTEM_ADMIN" &&
    [...SYSTEM_ADMIN_REQUIRED].some((code) => !permissions.has(code))
  ) {
    throw new BadRequestException("系统管理员核心权限不可移除");
  }
  if (
    roleCode !== "SYSTEM_ADMIN" &&
    permissionCodes.some((code) => SYSTEM_ADMIN_ONLY.has(code))
  ) {
    throw new BadRequestException(
      "用户、角色、范围、API Key、导入和系统配置权限仅限系统管理员角色",
    );
  }
  if (
    roleCode !== "RISK_ADMIN" &&
    permissionCodes.some((code) => RISK_ADMIN_ONLY.has(code))
  ) {
    throw new BadRequestException("个人邮箱配置与同步权限仅限风险管理员角色");
  }
  if (
    roleCode === "RISK_ADMIN" &&
    [...RISK_ADMIN_ONLY].some((code) => !permissions.has(code))
  ) {
    throw new BadRequestException("风险管理员必须保留个人邮箱配置与同步权限");
  }

  const allowedScopes: Record<string, DataScopeType[]> = {
    SYSTEM_ADMIN: [DataScopeType.ALL],
    RISK_ADMIN: [DataScopeType.ALL],
    PROJECT_MANAGER: [
      DataScopeType.OWNED,
      DataScopeType.ASSIGNED,
      DataScopeType.OWNED_OR_ASSIGNED,
      DataScopeType.NONE,
    ],
    VIEWER_AUDITOR: [DataScopeType.ASSIGNED, DataScopeType.NONE],
  };
  const allowed = allowedScopes[roleCode];
  if (allowed && !allowed.includes(dataScope)) {
    throw new BadRequestException("所选数据范围不符合该默认角色的权限边界");
  }
}
