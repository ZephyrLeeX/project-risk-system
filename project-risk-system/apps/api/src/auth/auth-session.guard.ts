import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
  UnauthorizedException,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import type { Request } from "express";

import { AuthService } from "./auth.service";
import type { AuthenticatedRequest } from "./auth.types";

const PASSWORD_CHANGE_ALLOWED_PATHS = new Set([
  "/api/auth/session",
  "/api/auth/change-password",
  "/api/auth/logout",
]);

export function canAccessWhilePasswordChangeRequired(
  originalUrl: string,
): boolean {
  const path = originalUrl.split("?")[0]?.replace(/\/+$/, "");
  return Boolean(path && PASSWORD_CHANGE_ALLOWED_PATHS.has(path));
}

@Injectable()
export class AuthSessionGuard implements CanActivate {
  constructor(
    private readonly authService: AuthService,
    private readonly config: ConfigService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<Request>();
    const cookieName = this.config.get<string>(
      "SESSION_COOKIE_NAME",
      "project_risk_session",
    );
    const token = request.cookies?.[cookieName] as string | undefined;

    if (!token) {
      throw new UnauthorizedException("登录状态已失效，请重新登录");
    }

    const identity = await this.authService.getSessionIdentity(token);
    if (!identity) {
      throw new UnauthorizedException("登录状态已失效，请重新登录");
    }

    if (
      identity.user.mustChangePassword &&
      !canAccessWhilePasswordChangeRequired(request.originalUrl)
    ) {
      throw new ForbiddenException("请先修改初始密码");
    }

    (request as AuthenticatedRequest).auth = identity;
    return true;
  }
}
