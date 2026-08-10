import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
} from "@nestjs/common";
import { Reflector } from "@nestjs/core";

import type { AuthenticatedRequest } from "../auth/auth.types";
import {
  REQUIRED_PERMISSIONS_KEY,
} from "./permissions.decorator";

export function hasAllPermissions(
  granted: string[],
  required: string[],
): boolean {
  return required.every((permission) => granted.includes(permission));
}

@Injectable()
export class PermissionGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const required = this.reflector.getAllAndOverride<string[]>(
      REQUIRED_PERMISSIONS_KEY,
      [context.getHandler(), context.getClass()],
    );
    if (!required || required.length === 0) {
      return true;
    }

    const request = context
      .switchToHttp()
      .getRequest<AuthenticatedRequest>();
    if (
      !request.auth ||
      !hasAllPermissions(request.auth.user.permissions, required)
    ) {
      throw new ForbiddenException("当前账号无权执行此操作");
    }

    return true;
  }
}
