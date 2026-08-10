import {
  Body,
  Controller,
  Get,
  Post,
  Req,
  Res,
  UseGuards,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import { randomUUID } from "node:crypto";
import type { Request, Response } from "express";

import type {
  ApiResponse,
  LoginResponse,
  SessionResponse,
} from "@risk-platform/contracts";

import { AuthSessionGuard } from "./auth-session.guard";
import { AuthService } from "./auth.service";
import type { AuthenticatedRequest } from "./auth.types";
import { ChangePasswordDto } from "./dto/change-password.dto";
import { LoginDto } from "./dto/login.dto";

@Controller("auth")
export class AuthController {
  constructor(
    private readonly authService: AuthService,
    private readonly config: ConfigService,
  ) {}

  @Post("login")
  async login(
    @Body() dto: LoginDto,
    @Req() request: Request,
    @Res({ passthrough: true }) response: Response,
  ): Promise<ApiResponse<LoginResponse>> {
    const result = await this.authService.login(
      {
        ...dto,
        username: dto.username.trim().toLocaleLowerCase(),
      },
      this.getRequestContext(request),
    );

    response.cookie(this.cookieName, result.token, {
      httpOnly: true,
      secure: this.isProduction,
      sameSite: "lax",
      path: "/",
      expires: result.expiresAt,
    });

    return {
      code: "OK",
      message: result.user.mustChangePassword
        ? "登录成功，请先修改初始密码"
        : "登录成功",
      data: {
        user: result.user,
        expiresAt: result.expiresAt.toISOString(),
      },
      traceId: randomUUID(),
    };
  }

  @Get("session")
  @UseGuards(AuthSessionGuard)
  getSession(
    @Req() request: AuthenticatedRequest,
  ): ApiResponse<SessionResponse> {
    return {
      code: "OK",
      message: "会话有效",
      data: {
        user: request.auth.user,
        expiresAt: request.auth.expiresAt.toISOString(),
      },
      traceId: randomUUID(),
    };
  }

  @Post("change-password")
  @UseGuards(AuthSessionGuard)
  async changePassword(
    @Body() dto: ChangePasswordDto,
    @Req() request: AuthenticatedRequest,
    @Res({ passthrough: true }) response: Response,
  ): Promise<ApiResponse<{ reloginRequired: true }>> {
    await this.authService.changePassword(
      request.auth,
      dto,
      this.getRequestContext(request),
    );
    this.clearSessionCookie(response);

    return {
      code: "OK",
      message: "密码修改成功，请使用新密码重新登录",
      data: { reloginRequired: true },
      traceId: randomUUID(),
    };
  }

  @Post("logout")
  @UseGuards(AuthSessionGuard)
  async logout(
    @Req() request: AuthenticatedRequest,
    @Res({ passthrough: true }) response: Response,
  ): Promise<ApiResponse<null>> {
    await this.authService.logout(
      request.auth,
      this.getRequestContext(request),
    );
    this.clearSessionCookie(response);

    return {
      code: "OK",
      message: "已安全退出",
      data: null,
      traceId: randomUUID(),
    };
  }

  private getRequestContext(request: Request): {
    clientIp?: string;
    userAgent?: string;
  } {
    return {
      clientIp: request.ip || request.socket.remoteAddress,
      userAgent: request.get("user-agent"),
    };
  }

  private clearSessionCookie(response: Response): void {
    response.clearCookie(this.cookieName, {
      httpOnly: true,
      secure: this.isProduction,
      sameSite: "lax",
      path: "/",
    });
  }

  private get cookieName(): string {
    return this.config.get<string>(
      "SESSION_COOKIE_NAME",
      "project_risk_session",
    );
  }

  private get isProduction(): boolean {
    return this.config.get<string>("NODE_ENV") === "production";
  }
}
