import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import { AuditResult, Prisma } from "@prisma/client";
import { randomUUID } from "node:crypto";

import type {
  PermissionItem,
  RoleListItem,
} from "@risk-platform/contracts";

import { AuditService } from "../audit/audit.service";
import { PrismaService } from "../prisma/prisma.service";
import {
  mapRole,
  ROLE_INCLUDE,
  type RoleWithAccess,
} from "./admin.mapper";
import type { AdminRequestContext } from "./admin.types";
import type {
  CreateRoleDto,
  UpdateRoleDto,
} from "./dto/role.dto";
import { validateRolePolicy } from "./role-policy";

@Injectable()
export class RolesService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
  ) {}

  async list(): Promise<RoleListItem[]> {
    const roles = await this.prisma.role.findMany({
      include: ROLE_INCLUDE,
      orderBy: [{ isSystem: "desc" }, { createdAt: "asc" }],
    });
    return roles.map(mapRole);
  }

  async permissions(): Promise<PermissionItem[]> {
    return this.prisma.permission.findMany({
      orderBy: [{ module: "asc" }, { code: "asc" }],
    });
  }

  async create(
    dto: CreateRoleDto,
    context: AdminRequestContext,
  ): Promise<RoleListItem> {
    const code = dto.code.trim().toLocaleUpperCase();
    const duplicate = await this.prisma.role.findUnique({
      where: { code },
      select: { id: true },
    });
    if (duplicate) {
      throw new ConflictException("角色编码已存在");
    }
    const permissions = await this.getPermissionsOrThrow(
      dto.permissionCodes,
    );
    validateRolePolicy(code, dto.permissionCodes, dto.defaultDataScope);

    const role = await this.prisma.$transaction(async (transaction) => {
      const created = await transaction.role.create({
        data: {
          code,
          name: dto.name.trim(),
          description: this.normalizeOptional(dto.description),
          isSystem: false,
          enabled: dto.enabled,
          defaultDataScope: dto.defaultDataScope,
        },
      });
      if (permissions.length > 0) {
        await transaction.rolePermission.createMany({
          data: permissions.map(({ id }) => ({
            roleId: created.id,
            permissionId: id,
          })),
        });
      }
      return transaction.role.findUniqueOrThrow({
        where: { id: created.id },
        include: ROLE_INCLUDE,
      });
    });
    await this.recordAudit(
      "ADMIN_ROLE_CREATED",
      role,
      context,
      this.snapshot(role),
    );
    return mapRole(role);
  }

  async update(
    id: string,
    dto: UpdateRoleDto,
    context: AdminRequestContext,
  ): Promise<RoleListItem> {
    const existing = await this.getRoleOrThrow(id);
    if (existing.isSystem && !dto.enabled) {
      throw new BadRequestException("系统预置角色不可停用");
    }
    const permissions = await this.getPermissionsOrThrow(
      dto.permissionCodes,
    );
    validateRolePolicy(
      existing.code,
      dto.permissionCodes,
      dto.defaultDataScope,
    );

    const role = await this.prisma.$transaction(async (transaction) => {
      await transaction.role.update({
        where: { id },
        data: {
          name: dto.name.trim(),
          description: this.normalizeOptional(dto.description),
          enabled: dto.enabled,
          defaultDataScope: dto.defaultDataScope,
        },
      });
      await transaction.rolePermission.deleteMany({ where: { roleId: id } });
      if (permissions.length > 0) {
        await transaction.rolePermission.createMany({
          data: permissions.map(({ id: permissionId }) => ({
            roleId: id,
            permissionId,
          })),
        });
      }
      return transaction.role.findUniqueOrThrow({
        where: { id },
        include: ROLE_INCLUDE,
      });
    });
    await this.recordAudit(
      "ADMIN_ROLE_UPDATED",
      role,
      context,
      {
        before: this.snapshot(existing),
        after: this.snapshot(role),
      },
    );
    return mapRole(role);
  }

  async remove(id: string, context: AdminRequestContext): Promise<void> {
    const role = await this.getRoleOrThrow(id);
    if (role.isSystem) {
      throw new BadRequestException("系统预置角色不可删除");
    }
    if (role._count.users > 0) {
      throw new ConflictException("角色仍有关联用户，请先迁移用户");
    }
    await this.prisma.role.delete({ where: { id } });
    await this.recordAudit(
      "ADMIN_ROLE_DELETED",
      role,
      context,
      this.snapshot(role),
    );
  }

  private async getRoleOrThrow(id: string): Promise<RoleWithAccess> {
    const role = await this.prisma.role.findUnique({
      where: { id },
      include: ROLE_INCLUDE,
    });
    if (!role) {
      throw new NotFoundException("角色不存在");
    }
    return role;
  }

  private async getPermissionsOrThrow(codes: string[]) {
    const permissions = await this.prisma.permission.findMany({
      where: { code: { in: codes } },
    });
    if (permissions.length !== codes.length) {
      throw new BadRequestException("权限列表中包含不存在的权限编码");
    }
    return permissions;
  }

  private normalizeOptional(value?: string | null): string | null {
    const normalized = value?.trim();
    return normalized ? normalized : null;
  }

  private snapshot(role: RoleWithAccess): Prisma.InputJsonValue {
    return {
      code: role.code,
      name: role.name,
      description: role.description,
      enabled: role.enabled,
      defaultDataScope: role.defaultDataScope,
      permissionCodes: role.permissions
        .map(({ permission }) => permission.code)
        .sort(),
    };
  }

  private async recordAudit(
    action: string,
    role: RoleWithAccess,
    context: AdminRequestContext,
    snapshot: Prisma.InputJsonValue,
  ): Promise<void> {
    await this.audit.record({
      actorUserId: context.identity.user.id,
      module: "ADMIN_ROLE",
      action,
      resourceType: "ROLE",
      resourceId: role.id,
      result: AuditResult.SUCCESS,
      traceId: randomUUID(),
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      afterSnapshot: snapshot,
    });
  }
}
