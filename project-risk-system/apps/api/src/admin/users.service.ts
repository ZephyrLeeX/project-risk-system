import {
  BadRequestException,
  ConflictException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  AuditResult,
  DataScopeType,
  Prisma,
  ProjectScopeSource,
  UserStatus,
} from "@prisma/client";
import argon2 = require("argon2");
import { randomBytes, randomUUID } from "node:crypto";

import type {
  AdminUserListItem,
  AdminUserSummary,
  PaginatedResponse,
  UserAuditRecord,
  UserMutationResponse,
} from "@risk-platform/contracts";

import { AuditService } from "../audit/audit.service";
import { PrismaService } from "../prisma/prisma.service";
import {
  mapUser,
  USER_INCLUDE,
  type UserWithAdminAccess,
} from "./admin.mapper";
import type { AdminRequestContext } from "./admin.types";
import type {
  CreateUserDto,
  ListUsersQueryDto,
  SetProjectScopesDto,
  UpdateUserDto,
} from "./dto/user.dto";
import { validateRolePolicy } from "./role-policy";

@Injectable()
export class UsersService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
  ) {}

  async list(
    query: ListUsersQueryDto,
  ): Promise<PaginatedResponse<AdminUserListItem>> {
    const keyword = query.keyword?.trim();
    const where: Prisma.UserWhereInput = {
      ...(query.status ? { status: query.status } : {}),
      ...(query.departmentId ? { departmentId: query.departmentId } : {}),
      ...(query.roleCode
        ? { roles: { some: { role: { code: query.roleCode } } } }
        : {}),
      ...(keyword
        ? {
            OR: [
              { displayName: { contains: keyword, mode: "insensitive" } },
              { username: { contains: keyword, mode: "insensitive" } },
              {
                department: {
                  name: { contains: keyword, mode: "insensitive" },
                },
              },
            ],
          }
        : {}),
    };
    const [items, total] = await this.prisma.$transaction([
      this.prisma.user.findMany({
        where,
        include: USER_INCLUDE,
        orderBy: [{ status: "asc" }, { createdAt: "asc" }],
        skip: (query.page - 1) * query.pageSize,
        take: query.pageSize,
      }),
      this.prisma.user.count({ where }),
    ]);

    return {
      items: items.map(mapUser),
      page: query.page,
      pageSize: query.pageSize,
      total,
    };
  }

  async summary(): Promise<AdminUserSummary> {
    const [total, active, locked, disabled] =
      await this.prisma.$transaction([
        this.prisma.user.count(),
        this.prisma.user.count({ where: { status: UserStatus.ACTIVE } }),
        this.prisma.user.count({ where: { status: UserStatus.LOCKED } }),
        this.prisma.user.count({ where: { status: UserStatus.DISABLED } }),
      ]);

    return { total, active, locked, disabled };
  }

  async get(id: string): Promise<AdminUserListItem> {
    return mapUser(await this.getUserOrThrow(id));
  }

  async create(
    dto: CreateUserDto,
    context: AdminRequestContext,
  ): Promise<UserMutationResponse> {
    const username = dto.username.trim().toLocaleLowerCase();
    await this.ensureUsernameAvailable(username);
    const role = await this.getRoleOrThrow(dto.roleId);
    this.validateScopeForRole(role, dto.dataScope);
    await this.validateProjectScopes(dto.dataScope, dto.projectIds);

    const initialPassword = this.generateInitialPassword();
    const passwordHash = await argon2.hash(initialPassword, {
      type: argon2.argon2id,
    });

    const user = await this.prisma.$transaction(async (transaction) => {
      const created = await transaction.user.create({
        data: {
          username,
          displayName: dto.displayName.trim(),
          email: this.normalizeOptional(dto.email),
          passwordHash,
          departmentId: dto.departmentId,
          status: dto.enabled ? UserStatus.ACTIVE : UserStatus.DISABLED,
          mustChangePassword: true,
        },
      });
      await transaction.userRole.create({
        data: {
          userId: created.id,
          roleId: role.id,
          dataScope: dto.dataScope,
        },
      });
      await this.replaceProjectScopes(
        transaction,
        created.id,
        dto.projectIds,
        context.identity.user.id,
      );
      return transaction.user.findUniqueOrThrow({
        where: { id: created.id },
        include: USER_INCLUDE,
      });
    });

    await this.recordUserAudit(
      "ADMIN_USER_CREATED",
      user,
      context,
      {
        username: user.username,
        displayName: user.displayName,
        roleCode: role.code,
        dataScope: dto.dataScope,
        projectCount: dto.projectIds.length,
        enabled: dto.enabled,
      },
    );

    return {
      user: mapUser(user),
      initialPassword,
    };
  }

  async update(
    id: string,
    dto: UpdateUserDto,
    context: AdminRequestContext,
  ): Promise<UserMutationResponse> {
    const existing = await this.getUserOrThrow(id);
    const username = dto.username.trim().toLocaleLowerCase();
    await this.ensureUsernameAvailable(username, id);
    const role = await this.getRoleOrThrow(dto.roleId);
    this.validateScopeForRole(role, dto.dataScope);
    await this.validateProjectScopes(dto.dataScope, dto.projectIds);

    const existingRole = existing.roles[0];
    if (
      id === context.identity.user.id &&
      (role.id !== existingRole?.roleId ||
        dto.dataScope !== existingRole.dataScope ||
        !dto.enabled)
    ) {
      throw new ForbiddenException(
        "不能修改当前登录账号自身的角色、数据范围或启用状态",
      );
    }

    const user = await this.prisma.$transaction(async (transaction) => {
      await transaction.user.update({
        where: { id },
        data: {
          username,
          displayName: dto.displayName.trim(),
          email: this.normalizeOptional(dto.email),
          departmentId: dto.departmentId,
          status: dto.enabled ? UserStatus.ACTIVE : UserStatus.DISABLED,
          ...(dto.enabled
            ? {}
            : {
                failedLoginCount: 0,
                lockedUntil: null,
              }),
        },
      });
      await transaction.userRole.deleteMany({ where: { userId: id } });
      await transaction.userRole.create({
        data: {
          userId: id,
          roleId: role.id,
          dataScope: dto.dataScope,
        },
      });
      await this.replaceProjectScopes(
        transaction,
        id,
        dto.projectIds,
        context.identity.user.id,
      );
      if (!dto.enabled) {
        await transaction.session.updateMany({
          where: { userId: id, revokedAt: null },
          data: { revokedAt: new Date() },
        });
      }
      return transaction.user.findUniqueOrThrow({
        where: { id },
        include: USER_INCLUDE,
      });
    });

    await this.recordUserAudit(
      "ADMIN_USER_UPDATED",
      user,
      context,
      {
        before: this.snapshot(existing),
        after: this.snapshot(user),
      },
    );
    return { user: mapUser(user) };
  }

  async setStatus(
    id: string,
    status: UserStatus,
    context: AdminRequestContext,
  ): Promise<AdminUserListItem> {
    if (
      status !== UserStatus.ACTIVE &&
      status !== UserStatus.DISABLED
    ) {
      throw new BadRequestException("账号状态仅支持启用或停用");
    }
    if (id === context.identity.user.id && status === UserStatus.DISABLED) {
      throw new ForbiddenException("不能停用当前登录账号");
    }
    await this.getUserOrThrow(id);

    const user = await this.prisma.$transaction(async (transaction) => {
      await transaction.user.update({
        where: { id },
        data: {
          status,
          failedLoginCount: 0,
          lockedUntil: null,
        },
      });
      if (status === UserStatus.DISABLED) {
        await transaction.session.updateMany({
          where: { userId: id, revokedAt: null },
          data: { revokedAt: new Date() },
        });
      }
      return transaction.user.findUniqueOrThrow({
        where: { id },
        include: USER_INCLUDE,
      });
    });

    await this.recordUserAudit(
      status === UserStatus.ACTIVE
        ? "ADMIN_USER_ENABLED"
        : "ADMIN_USER_DISABLED",
      user,
      context,
      { status },
    );
    return mapUser(user);
  }

  async unlock(
    id: string,
    context: AdminRequestContext,
  ): Promise<AdminUserListItem> {
    await this.getUserOrThrow(id);
    const user = await this.prisma.user.update({
      where: { id },
      data: {
        status: UserStatus.ACTIVE,
        failedLoginCount: 0,
        lockedUntil: null,
      },
      include: USER_INCLUDE,
    });
    await this.recordUserAudit(
      "ADMIN_USER_UNLOCKED",
      user,
      context,
      { status: UserStatus.ACTIVE },
    );
    return mapUser(user);
  }

  async resetPassword(
    id: string,
    context: AdminRequestContext,
  ): Promise<{ initialPassword: string }> {
    const user = await this.getUserOrThrow(id);
    const initialPassword = this.generateInitialPassword();
    const passwordHash = await argon2.hash(initialPassword, {
      type: argon2.argon2id,
    });

    await this.prisma.$transaction([
      this.prisma.user.update({
        where: { id },
        data: {
          passwordHash,
          mustChangePassword: true,
          passwordChangedAt: null,
          failedLoginCount: 0,
          lockedUntil: null,
          status:
            user.status === UserStatus.DISABLED
              ? UserStatus.DISABLED
              : UserStatus.ACTIVE,
        },
      }),
      this.prisma.session.updateMany({
        where: { userId: id, revokedAt: null },
        data: { revokedAt: new Date() },
      }),
    ]);
    await this.recordUserAudit(
      "ADMIN_USER_PASSWORD_RESET",
      user,
      context,
      { mustChangePassword: true },
    );

    return { initialPassword };
  }

  async getProjectScopes(id: string): Promise<{
    dataScope: DataScopeType;
    projectIds: string[];
  }> {
    const user = await this.getUserOrThrow(id);
    return {
      dataScope: user.roles[0]?.dataScope ?? DataScopeType.NONE,
      projectIds: user.projectScopes.map(({ projectId }) => projectId),
    };
  }

  async setProjectScopes(
    id: string,
    dto: SetProjectScopesDto,
    context: AdminRequestContext,
  ): Promise<AdminUserListItem> {
    const user = await this.getUserOrThrow(id);
    const userRole = user.roles[0];
    if (!userRole) {
      throw new ConflictException("用户尚未分配角色");
    }
    this.validateScopeForRole(userRole.role, dto.dataScope);
    await this.validateProjectScopes(dto.dataScope, dto.projectIds);
    if (
      id === context.identity.user.id &&
      dto.dataScope !== userRole.dataScope
    ) {
      throw new ForbiddenException("不能修改当前登录账号自身的数据范围");
    }

    const updated = await this.prisma.$transaction(
      async (transaction) => {
        await transaction.userRole.update({
          where: {
            userId_roleId: {
              userId: id,
              roleId: userRole.roleId,
            },
          },
          data: { dataScope: dto.dataScope },
        });
        await this.replaceProjectScopes(
          transaction,
          id,
          dto.projectIds,
          context.identity.user.id,
        );
        return transaction.user.findUniqueOrThrow({
          where: { id },
          include: USER_INCLUDE,
        });
      },
    );
    await this.recordUserAudit(
      "ADMIN_USER_SCOPE_UPDATED",
      updated,
      context,
      {
        before: {
          dataScope: userRole.dataScope,
          projectIds: user.projectScopes.map(({ projectId }) => projectId),
        },
        after: {
          dataScope: dto.dataScope,
          projectIds: dto.projectIds,
        },
      },
    );
    return mapUser(updated);
  }

  async records(id: string): Promise<UserAuditRecord[]> {
    await this.getUserOrThrow(id);
    const events = await this.prisma.auditLog.findMany({
      where: {
        resourceType: "USER",
        resourceId: id,
      },
      include: {
        actor: {
          select: { displayName: true },
        },
      },
      orderBy: { createdAt: "desc" },
      take: 100,
    });

    return events.map((event) => ({
      id: event.id,
      action: event.action,
      result: event.result,
      actorName: event.actor?.displayName ?? null,
      createdAt: event.createdAt.toISOString(),
      summary: this.auditSummary(event.action),
    }));
  }

  private async getUserOrThrow(id: string): Promise<UserWithAdminAccess> {
    const user = await this.prisma.user.findUnique({
      where: { id },
      include: USER_INCLUDE,
    });
    if (!user) {
      throw new NotFoundException("用户不存在");
    }
    return user;
  }

  private async ensureUsernameAvailable(
    username: string,
    excludeId?: string,
  ): Promise<void> {
    const existing = await this.prisma.user.findFirst({
      where: {
        username: { equals: username, mode: "insensitive" },
        ...(excludeId ? { id: { not: excludeId } } : {}),
      },
      select: { id: true },
    });
    if (existing) {
      throw new ConflictException("登录账号已存在");
    }
  }

  private async getRoleOrThrow(roleId: string) {
    const role = await this.prisma.role.findUnique({
      where: { id: roleId },
      include: {
        permissions: {
          include: { permission: true },
        },
      },
    });
    if (!role || !role.enabled) {
      throw new BadRequestException("所选角色不存在或已停用");
    }
    return role;
  }

  private validateScopeForRole(
    role: Awaited<ReturnType<UsersService["getRoleOrThrow"]>>,
    dataScope: DataScopeType,
  ): void {
    validateRolePolicy(
      role.code,
      role.permissions.map(({ permission }) => permission.code),
      dataScope,
    );
  }

  private async validateProjectScopes(
    dataScope: DataScopeType,
    projectIds: string[],
  ): Promise<void> {
    const usesAssignments =
      dataScope === DataScopeType.ASSIGNED ||
      dataScope === DataScopeType.OWNED_OR_ASSIGNED;
    if (!usesAssignments && projectIds.length > 0) {
      throw new BadRequestException("当前数据范围不使用指定项目授权");
    }
    if (dataScope === DataScopeType.ASSIGNED && projectIds.length === 0) {
      throw new BadRequestException("被授权项目范围至少选择一个项目");
    }
    if (projectIds.length > 0) {
      const existingCount = await this.prisma.project.count({
        where: {
          id: { in: projectIds },
        },
      });
      if (existingCount !== projectIds.length) {
        throw new BadRequestException("指定项目中包含不存在的项目");
      }
    }
  }

  private async replaceProjectScopes(
    transaction: Prisma.TransactionClient,
    userId: string,
    projectIds: string[],
    actorUserId: string,
  ): Promise<void> {
    await transaction.userProjectScope.deleteMany({ where: { userId } });
    if (projectIds.length > 0) {
      await transaction.userProjectScope.createMany({
        data: projectIds.map((projectId) => ({
          userId,
          projectId,
          assignedBy: actorUserId,
          scopeSource: ProjectScopeSource.ADMIN,
        })),
      });
    }
  }

  private generateInitialPassword(): string {
    return `Risk!${randomBytes(9).toString("base64url")}aA1`;
  }

  private normalizeOptional(value?: string | null): string | null {
    const normalized = value?.trim();
    return normalized ? normalized : null;
  }

  private async recordUserAudit(
    action: string,
    user: UserWithAdminAccess,
    context: AdminRequestContext,
    afterSnapshot: Prisma.InputJsonValue,
  ): Promise<void> {
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "ADMIN_USER",
      action,
      resourceType: "USER",
      resourceId: user.id,
      result: AuditResult.SUCCESS,
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      afterSnapshot,
    });
  }

  private snapshot(user: UserWithAdminAccess): Prisma.InputJsonValue {
    return {
      username: user.username,
      displayName: user.displayName,
      email: user.email,
      departmentId: user.departmentId,
      status: user.status,
      roleCode: user.roles[0]?.role.code ?? null,
      dataScope: user.roles[0]?.dataScope ?? DataScopeType.NONE,
      projectIds: user.projectScopes.map(({ projectId }) => projectId),
    };
  }

  private auditSummary(action: string): string {
    const summaries: Record<string, string> = {
      ADMIN_USER_CREATED: "创建用户账号",
      ADMIN_USER_UPDATED: "更新账号、角色或项目范围",
      ADMIN_USER_ENABLED: "启用用户账号",
      ADMIN_USER_DISABLED: "停用用户账号并撤销会话",
      ADMIN_USER_UNLOCKED: "解除登录锁定",
      ADMIN_USER_PASSWORD_RESET: "重置初始密码并撤销会话",
      ADMIN_USER_SCOPE_UPDATED: "更新项目数据范围",
    };
    return summaries[action] ?? action;
  }
}
