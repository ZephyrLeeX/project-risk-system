import {
  BadRequestException,
  HttpException,
  Injectable,
  UnauthorizedException,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import {
  AuditResult,
  DataScopeType as PrismaDataScopeType,
  Prisma,
  UserStatus,
} from "@prisma/client";
import {
  createHash,
  createHmac,
  randomBytes,
  randomUUID,
} from "node:crypto";
import argon2 = require("argon2");

import {
  DATA_SCOPE_TYPES,
  ROLE_CODES,
  type AuthenticatedUser,
  type DataScopeType,
  type RoleCode,
} from "@risk-platform/contracts";

import { AuditService } from "../audit/audit.service";
import { PrismaService } from "../prisma/prisma.service";
import type { ChangePasswordDto } from "./dto/change-password.dto";
import type { LoginDto } from "./dto/login.dto";
import { getPasswordPolicyViolations } from "./password-policy";
import type { SessionIdentity } from "./auth.types";

type UserWithAccess = Prisma.UserGetPayload<{
  include: {
    department: true;
    roles: {
      include: {
        role: {
          include: {
            permissions: {
              include: {
                permission: true;
              };
            };
          };
        };
      };
    };
  };
}>;

const ACCESS_INCLUDE = {
  department: true,
  roles: {
    include: {
      role: {
        include: {
          permissions: {
            include: {
              permission: true,
            },
          },
        },
      },
    },
  },
} satisfies Prisma.UserInclude;

export interface LoginContext {
  clientIp?: string;
  userAgent?: string;
}

export interface LoginResult {
  token: string;
  expiresAt: Date;
  user: AuthenticatedUser;
}

@Injectable()
export class AuthService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
    private readonly audit: AuditService,
  ) {}

  async login(dto: LoginDto, context: LoginContext): Promise<LoginResult> {
    const traceId = randomUUID();
    const username = dto.username.trim().toLocaleLowerCase();
    const user = await this.prisma.user.findFirst({
      where: { username },
      include: ACCESS_INCLUDE,
    });

    if (!user) {
      await this.audit.record({
        module: "AUTH",
        action: "AUTH_LOGIN_FAILED",
        resourceType: "USER",
        resourceId: username,
        result: AuditResult.FAILURE,
        traceId,
        clientIp: context.clientIp,
        userAgent: context.userAgent,
        errorCode: "INVALID_CREDENTIALS",
      });
      throw new UnauthorizedException("账号或密码错误");
    }

    if (user.status === UserStatus.DISABLED) {
      await this.recordLoginFailure(
        user,
        traceId,
        context,
        "ACCOUNT_DISABLED",
      );
      throw new UnauthorizedException("账号或密码错误");
    }

    if (
      user.status === UserStatus.LOCKED &&
      user.lockedUntil &&
      user.lockedUntil > new Date()
    ) {
      await this.recordLoginFailure(
        user,
        traceId,
        context,
        "ACCOUNT_LOCKED",
      );
      throw new HttpException(
        `账号已锁定，请于 ${user.lockedUntil.toLocaleString("zh-CN")} 后重试`,
        423,
      );
    }

    if (
      user.status === UserStatus.LOCKED &&
      (!user.lockedUntil || user.lockedUntil <= new Date())
    ) {
      await this.prisma.user.update({
        where: { id: user.id },
        data: {
          status: UserStatus.ACTIVE,
          failedLoginCount: 0,
          lockedUntil: null,
        },
      });
    }

    const passwordValid = await argon2.verify(
      user.passwordHash,
      dto.password,
    );

    if (!passwordValid) {
      await this.handleInvalidPassword(user, traceId, context);
      throw new UnauthorizedException("账号或密码错误");
    }

    const token = randomBytes(32).toString("base64url");
    const runtimeSecurity = await this.runtimeSecurity();
    const expiresAt = new Date(
      Date.now() + runtimeSecurity.sessionHours * 60 * 60 * 1000,
    );

    const [, session] = await this.prisma.$transaction([
      this.prisma.user.update({
        where: { id: user.id },
        data: {
          status: UserStatus.ACTIVE,
          failedLoginCount: 0,
          lockedUntil: null,
          lastLoginAt: new Date(),
        },
      }),
      this.prisma.session.create({
        data: {
          tokenHash: this.hashToken(token),
          userId: user.id,
          expiresAt,
          clientIpHash: context.clientIp
            ? this.hashClientIp(context.clientIp)
            : null,
          userAgent: context.userAgent?.slice(0, 500),
        },
      }),
    ]);

    await this.audit.record({
      actorUserId: user.id,
      module: "AUTH",
      action: "AUTH_LOGIN_SUCCESS",
      resourceType: "SESSION",
      resourceId: session.id,
      result: AuditResult.SUCCESS,
      traceId,
      clientIp: context.clientIp,
      userAgent: context.userAgent,
    });

    return {
      token,
      expiresAt,
      user: this.toAuthenticatedUser(user),
    };
  }

  async getSessionIdentity(token: string): Promise<SessionIdentity | null> {
    const session = await this.prisma.session.findUnique({
      where: { tokenHash: this.hashToken(token) },
      include: {
        user: {
          include: ACCESS_INCLUDE,
        },
      },
    });

    if (
      !session ||
      session.revokedAt ||
      session.expiresAt <= new Date() ||
      session.user.status !== UserStatus.ACTIVE
    ) {
      return null;
    }

    return {
      sessionId: session.id,
      expiresAt: session.expiresAt,
      user: this.toAuthenticatedUser(session.user),
    };
  }

  async changePassword(
    identity: SessionIdentity,
    dto: ChangePasswordDto,
    context: LoginContext,
  ): Promise<void> {
    const traceId = randomUUID();

    if (dto.newPassword !== dto.confirmPassword) {
      throw new BadRequestException("两次输入的新密码不一致");
    }

    const user = await this.prisma.user.findUnique({
      where: { id: identity.user.id },
    });
    if (!user || user.status !== UserStatus.ACTIVE) {
      throw new UnauthorizedException("登录状态已失效，请重新登录");
    }

    const currentPasswordValid = await argon2.verify(
      user.passwordHash,
      dto.currentPassword,
    );
    if (!currentPasswordValid) {
      await this.audit.record({
        actorUserId: user.id,
        module: "AUTH",
        action: "AUTH_PASSWORD_CHANGE_FAILED",
        resourceType: "USER",
        resourceId: user.id,
        result: AuditResult.FAILURE,
        traceId,
        clientIp: context.clientIp,
        userAgent: context.userAgent,
        errorCode: "CURRENT_PASSWORD_INVALID",
      });
      throw new BadRequestException("当前密码不正确");
    }

    if (await argon2.verify(user.passwordHash, dto.newPassword)) {
      throw new BadRequestException("新密码不能与当前密码相同");
    }

    const runtimeSecurity = await this.runtimeSecurity();
    const violations = getPasswordPolicyViolations(dto.newPassword, {
      minLength: runtimeSecurity.passwordMinLength,
      username: user.username,
    });
    if (violations.length > 0) {
      throw new BadRequestException(violations.join("；"));
    }

    const passwordHash = await argon2.hash(dto.newPassword, {
      type: argon2.argon2id,
    });

    await this.prisma.$transaction([
      this.prisma.user.update({
        where: { id: user.id },
        data: {
          passwordHash,
          mustChangePassword: false,
          passwordChangedAt: new Date(),
          failedLoginCount: 0,
          lockedUntil: null,
        },
      }),
      this.prisma.session.updateMany({
        where: {
          userId: user.id,
          revokedAt: null,
        },
        data: { revokedAt: new Date() },
      }),
    ]);

    await this.audit.record({
      actorUserId: user.id,
      module: "AUTH",
      action: "AUTH_PASSWORD_CHANGED",
      resourceType: "USER",
      resourceId: user.id,
      result: AuditResult.SUCCESS,
      traceId,
      clientIp: context.clientIp,
      userAgent: context.userAgent,
    });
  }

  async logout(
    identity: SessionIdentity,
    context: LoginContext,
  ): Promise<void> {
    const traceId = randomUUID();
    await this.prisma.session.updateMany({
      where: {
        id: identity.sessionId,
        revokedAt: null,
      },
      data: { revokedAt: new Date() },
    });
    await this.audit.record({
      actorUserId: identity.user.id,
      module: "AUTH",
      action: "AUTH_LOGOUT",
      resourceType: "SESSION",
      resourceId: identity.sessionId,
      result: AuditResult.SUCCESS,
      traceId,
      clientIp: context.clientIp,
      userAgent: context.userAgent,
    });
  }

  private async handleInvalidPassword(
    user: UserWithAccess,
    traceId: string,
    context: LoginContext,
  ): Promise<void> {
    const runtimeSecurity = await this.runtimeSecurity();
    const maxAttempts = runtimeSecurity.loginMaxAttempts;
    const failedLoginCount = user.failedLoginCount + 1;
    const shouldLock = failedLoginCount >= maxAttempts;
    const lockedUntil = shouldLock
      ? new Date(
          Date.now() +
            runtimeSecurity.loginLockMinutes * 60 * 1000,
        )
      : null;

    await this.prisma.user.update({
      where: { id: user.id },
      data: {
        failedLoginCount,
        status: shouldLock ? UserStatus.LOCKED : UserStatus.ACTIVE,
        lockedUntil,
      },
    });

    await this.audit.record({
      actorUserId: user.id,
      module: "AUTH",
      action: shouldLock ? "AUTH_ACCOUNT_LOCKED" : "AUTH_LOGIN_FAILED",
      resourceType: "USER",
      resourceId: user.id,
      result: AuditResult.FAILURE,
      traceId,
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      errorCode: shouldLock ? "ACCOUNT_LOCKED" : "INVALID_CREDENTIALS",
      afterSnapshot: {
        failedLoginCount,
        lockedUntil: lockedUntil?.toISOString() ?? null,
      },
    });
  }

  private async recordLoginFailure(
    user: UserWithAccess,
    traceId: string,
    context: LoginContext,
    errorCode: string,
  ): Promise<void> {
    await this.audit.record({
      actorUserId: user.id,
      module: "AUTH",
      action: "AUTH_LOGIN_FAILED",
      resourceType: "USER",
      resourceId: user.id,
      result: AuditResult.FAILURE,
      traceId,
      clientIp: context.clientIp,
      userAgent: context.userAgent,
      errorCode,
    });
  }

  private toAuthenticatedUser(user: UserWithAccess): AuthenticatedUser {
    const enabledRoles = user.roles.filter(({ role }) => role.enabled);
    const roleCodes = enabledRoles
      .map(({ role }) => role.code)
      .filter((code): code is RoleCode =>
        ROLE_CODES.includes(code as RoleCode),
      );
    const permissions = [
      ...new Set(
        enabledRoles.flatMap(({ role }) =>
          role.permissions.map(({ permission }) => permission.code),
        ),
      ),
    ].sort();
    const dataScopes = enabledRoles
      .map(({ dataScope }) => dataScope)
      .filter((scope): scope is PrismaDataScopeType =>
        DATA_SCOPE_TYPES.includes(scope as DataScopeType),
      );

    return {
      id: user.id,
      username: user.username,
      displayName: user.displayName,
      departmentName: user.department?.name ?? null,
      roleCodes: [...new Set(roleCodes)],
      permissions,
      dataScope: this.aggregateDataScope(dataScopes),
      mustChangePassword: user.mustChangePassword,
    };
  }

  private aggregateDataScope(
    scopes: PrismaDataScopeType[],
  ): DataScopeType {
    if (scopes.includes(PrismaDataScopeType.ALL)) {
      return "ALL";
    }
    if (
      scopes.includes(PrismaDataScopeType.OWNED_OR_ASSIGNED) ||
      (scopes.includes(PrismaDataScopeType.OWNED) &&
        scopes.includes(PrismaDataScopeType.ASSIGNED))
    ) {
      return "OWNED_OR_ASSIGNED";
    }
    if (scopes.includes(PrismaDataScopeType.OWNED)) {
      return "OWNED";
    }
    if (scopes.includes(PrismaDataScopeType.ASSIGNED)) {
      return "ASSIGNED";
    }
    return "NONE";
  }

  private hashToken(token: string): string {
    return createHash("sha256").update(token).digest("hex");
  }

  private hashClientIp(clientIp: string): string {
    return createHmac(
      "sha256",
      this.config.get<string>("SESSION_SECRET", "development-only-secret"),
    )
      .update(clientIp)
      .digest("hex");
  }

  private async runtimeSecurity(): Promise<{
    sessionHours: number;
    loginMaxAttempts: number;
    loginLockMinutes: number;
    passwordMinLength: number;
  }> {
    const fallback = {
      sessionHours: this.config.get<number>("SESSION_TTL_HOURS", 8),
      loginMaxAttempts: this.config.get<number>("LOGIN_MAX_ATTEMPTS", 5),
      loginLockMinutes: this.config.get<number>("LOGIN_LOCK_MINUTES", 30),
      passwordMinLength: this.config.get<number>("PASSWORD_MIN_LENGTH", 12),
    };
    const latest = await this.prisma.systemConfigRelease.findFirst({
      orderBy: { publishedAt: "desc" },
      select: { snapshot: true },
    });
    const security = (latest?.snapshot as { security?: Partial<typeof fallback> } | null)
      ?.security;
    return { ...fallback, ...(security ?? {}) };
  }
}
