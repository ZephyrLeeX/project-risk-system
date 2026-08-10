import {
  Body,
  Controller,
  Delete,
  Get,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Put,
  Query,
  Req,
  UseGuards,
} from "@nestjs/common";
import { randomUUID } from "node:crypto";

import type {
  AdminUserListItem,
  AdminUserSummary,
  ApiResponse,
  DepartmentOption,
  PaginatedResponse,
  PermissionItem,
  ProjectOption,
  RoleListItem,
  UserAuditRecord,
  UserMutationResponse,
} from "@risk-platform/contracts";

import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import { AdminOptionsService } from "./admin-options.service";
import type { AdminRequestContext } from "./admin.types";
import { CreateRoleDto, UpdateRoleDto } from "./dto/role.dto";
import {
  CreateUserDto,
  ListUsersQueryDto,
  SetProjectScopesDto,
  SetUserStatusDto,
  UpdateUserDto,
} from "./dto/user.dto";
import { RolesService } from "./roles.service";
import { UsersService } from "./users.service";

@Controller("admin")
@UseGuards(AuthSessionGuard, PermissionGuard)
export class AdminController {
  constructor(
    private readonly users: UsersService,
    private readonly roles: RolesService,
    private readonly options: AdminOptionsService,
  ) {}

  @Get("users/summary")
  @RequirePermissions("admin.user.manage")
  async userSummary(): Promise<ApiResponse<AdminUserSummary>> {
    return this.ok(await this.users.summary());
  }

  @Get("users")
  @RequirePermissions("admin.user.manage")
  async listUsers(
    @Query() query: ListUsersQueryDto,
  ): Promise<ApiResponse<PaginatedResponse<AdminUserListItem>>> {
    return this.ok(await this.users.list(query));
  }

  @Post("users")
  @RequirePermissions("admin.user.manage", "admin.scope.manage")
  async createUser(
    @Body() dto: CreateUserDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<UserMutationResponse>> {
    return this.ok(
      await this.users.create(dto, this.context(request)),
      "用户创建成功，请安全转交一次性初始密码",
    );
  }

  @Get("users/:id")
  @RequirePermissions("admin.user.manage")
  async getUser(
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<AdminUserListItem>> {
    return this.ok(await this.users.get(id));
  }

  @Patch("users/:id")
  @RequirePermissions("admin.user.manage", "admin.scope.manage")
  async updateUser(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: UpdateUserDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<UserMutationResponse>> {
    return this.ok(
      await this.users.update(id, dto, this.context(request)),
      "用户信息已更新",
    );
  }

  @Post("users/:id/status")
  @RequirePermissions("admin.user.manage")
  async setUserStatus(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: SetUserStatusDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AdminUserListItem>> {
    return this.ok(
      await this.users.setStatus(
        id,
        dto.status,
        this.context(request),
      ),
      dto.status === "ACTIVE" ? "用户已启用" : "用户已停用",
    );
  }

  @Post("users/:id/unlock")
  @RequirePermissions("admin.user.manage")
  async unlockUser(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AdminUserListItem>> {
    return this.ok(
      await this.users.unlock(id, this.context(request)),
      "用户锁定已解除",
    );
  }

  @Post("users/:id/reset-password")
  @RequirePermissions("admin.user.manage")
  async resetPassword(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<{ initialPassword: string }>> {
    return this.ok(
      await this.users.resetPassword(id, this.context(request)),
      "密码已重置，原有会话已撤销",
    );
  }

  @Get("users/:id/records")
  @RequirePermissions("admin.user.manage")
  async userRecords(
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<UserAuditRecord[]>> {
    return this.ok(await this.users.records(id));
  }

  @Get("users/:id/project-scopes")
  @RequirePermissions("admin.scope.manage")
  async getProjectScopes(
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<
    ApiResponse<{ dataScope: string; projectIds: string[] }>
  > {
    return this.ok(await this.users.getProjectScopes(id));
  }

  @Put("users/:id/project-scopes")
  @RequirePermissions("admin.scope.manage")
  async setProjectScopes(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: SetProjectScopesDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<AdminUserListItem>> {
    return this.ok(
      await this.users.setProjectScopes(
        id,
        dto,
        this.context(request),
      ),
      "项目数据范围已更新",
    );
  }

  @Get("roles")
  @RequirePermissions("admin.role.manage")
  async listRoles(): Promise<ApiResponse<RoleListItem[]>> {
    return this.ok(await this.roles.list());
  }

  @Post("roles")
  @RequirePermissions("admin.role.manage")
  async createRole(
    @Body() dto: CreateRoleDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<RoleListItem>> {
    return this.ok(
      await this.roles.create(dto, this.context(request)),
      "角色创建成功",
    );
  }

  @Patch("roles/:id")
  @RequirePermissions("admin.role.manage")
  async updateRole(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: UpdateRoleDto,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<RoleListItem>> {
    return this.ok(
      await this.roles.update(id, dto, this.context(request)),
      "角色权限已保存并立即生效",
    );
  }

  @Delete("roles/:id")
  @RequirePermissions("admin.role.manage")
  async deleteRole(
    @Param("id", new ParseUUIDPipe()) id: string,
    @Req() request: AuthenticatedRequest,
  ): Promise<ApiResponse<null>> {
    await this.roles.remove(id, this.context(request));
    return this.ok(null, "角色已删除");
  }

  @Get("permissions")
  @RequirePermissions("admin.role.manage")
  async permissions(): Promise<ApiResponse<PermissionItem[]>> {
    return this.ok(await this.roles.permissions());
  }

  @Get("departments")
  @RequirePermissions("admin.user.manage")
  async departments(): Promise<ApiResponse<DepartmentOption[]>> {
    return this.ok(await this.options.departments());
  }

  @Get("projects/options")
  @RequirePermissions("admin.scope.manage")
  async projects(): Promise<ApiResponse<ProjectOption[]>> {
    return this.ok(await this.options.projects());
  }

  private context(request: AuthenticatedRequest): AdminRequestContext {
    return {
      identity: request.auth,
      clientIp: request.ip || request.socket.remoteAddress,
      userAgent: request.get("user-agent"),
    };
  }

  private ok<T>(data: T, message = "success"): ApiResponse<T> {
    return {
      code: "OK",
      message,
      data,
      traceId: randomUUID(),
    };
  }
}
