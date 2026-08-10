import { Prisma } from "@prisma/client";

import type {
  AdminUserListItem,
  RoleListItem,
} from "@risk-platform/contracts";

export const ROLE_INCLUDE = {
  permissions: {
    include: {
      permission: true,
    },
  },
  _count: {
    select: {
      users: true,
    },
  },
} satisfies Prisma.RoleInclude;

export const USER_INCLUDE = {
  department: true,
  roles: {
    include: {
      role: {
        include: ROLE_INCLUDE,
      },
    },
    orderBy: {
      assignedAt: "asc",
    },
  },
  projectScopes: {
    select: {
      projectId: true,
    },
  },
} satisfies Prisma.UserInclude;

export type RoleWithAccess = Prisma.RoleGetPayload<{
  include: typeof ROLE_INCLUDE;
}>;

export type UserWithAdminAccess = Prisma.UserGetPayload<{
  include: typeof USER_INCLUDE;
}>;

export function mapRole(role: RoleWithAccess): RoleListItem {
  return {
    id: role.id,
    code: role.code,
    name: role.name,
    description: role.description,
    isSystem: role.isSystem,
    enabled: role.enabled,
    defaultDataScope: role.defaultDataScope,
    userCount: role._count.users,
    permissionCodes: role.permissions
      .map(({ permission }) => permission.code)
      .sort(),
    updatedAt: role.updatedAt.toISOString(),
  };
}

export function mapUser(user: UserWithAdminAccess): AdminUserListItem {
  const userRole = user.roles[0];

  return {
    id: user.id,
    username: user.username,
    displayName: user.displayName,
    email: user.email,
    department: user.department
      ? {
          id: user.department.id,
          code: user.department.code,
          name: user.department.name,
        }
      : null,
    status: user.status,
    role: userRole ? mapRole(userRole.role) : null,
    dataScope: userRole?.dataScope ?? "NONE",
    assignedProjectIds: user.projectScopes.map(({ projectId }) => projectId),
    assignedProjectCount: user.projectScopes.length,
    mustChangePassword: user.mustChangePassword,
    lastLoginAt: user.lastLoginAt?.toISOString() ?? null,
    lockedUntil: user.lockedUntil?.toISOString() ?? null,
    createdAt: user.createdAt.toISOString(),
    updatedAt: user.updatedAt.toISOString(),
  };
}
