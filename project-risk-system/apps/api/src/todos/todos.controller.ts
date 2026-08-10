import {
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Patch,
  Query,
  Req,
  UseGuards,
} from "@nestjs/common";
import { randomUUID } from "node:crypto";

import type {
  ApiResponse,
  ManagerTodoDetail,
  ManagerTodoListResponse,
} from "@risk-platform/contracts";

import { AuthSessionGuard } from "../auth/auth-session.guard";
import type { AuthenticatedRequest } from "../auth/auth.types";
import { PermissionGuard } from "../rbac/permission.guard";
import { RequirePermissions } from "../rbac/permissions.decorator";
import { ListTodosQueryDto, UpdateTodoDto } from "./dto/todo.dto";
import { TodosService } from "./todos.service";

@Controller("todos")
@UseGuards(AuthSessionGuard, PermissionGuard)
@RequirePermissions("dashboard.view")
export class TodosController {
  constructor(private readonly todos: TodosService) {}

  @Get()
  async list(
    @Req() request: AuthenticatedRequest,
    @Query() query: ListTodosQueryDto,
  ): Promise<ApiResponse<ManagerTodoListResponse>> {
    return this.ok(await this.todos.list(request.auth, query));
  }

  @Get(":id")
  async detail(
    @Req() request: AuthenticatedRequest,
    @Param("id", new ParseUUIDPipe()) id: string,
  ): Promise<ApiResponse<ManagerTodoDetail>> {
    return this.ok(await this.todos.detail(request.auth, id));
  }

  @Patch(":id")
  @RequirePermissions("risk.resolve")
  async update(
    @Req() request: AuthenticatedRequest,
    @Param("id", new ParseUUIDPipe()) id: string,
    @Body() dto: UpdateTodoDto,
  ): Promise<ApiResponse<ManagerTodoDetail>> {
    return this.ok(
      await this.todos.update(id, dto, {
        identity: request.auth,
        clientIp: request.ip || request.socket.remoteAddress,
        userAgent: request.get("user-agent"),
      }),
      "待办事项已更新",
    );
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
