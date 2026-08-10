import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import type { Request } from "express";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

@Injectable()
export class CsrfOriginGuard implements CanActivate {
  constructor(private readonly config: ConfigService) {}

  canActivate(context: ExecutionContext): boolean {
    const request = context.switchToHttp().getRequest<Request>();
    if (SAFE_METHODS.has(request.method)) {
      return true;
    }

    const origin = request.get("origin");
    if (!origin) {
      return true;
    }

    const allowedOrigins = this.config
      .get<string>("CORS_ORIGIN", "http://localhost:5173")
      .split(",")
      .map((item) => item.trim());

    if (!allowedOrigins.includes(origin)) {
      throw new ForbiddenException("请求来源校验失败");
    }

    return true;
  }
}
