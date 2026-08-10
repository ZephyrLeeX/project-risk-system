import { Controller, Get } from "@nestjs/common";
import { randomUUID } from "node:crypto";

import type {
  ApiResponse,
  HealthResponse,
} from "@risk-platform/contracts";

import { HealthService } from "./health.service";

@Controller("health")
export class HealthController {
  constructor(private readonly healthService: HealthService) {}

  @Get()
  getHealth(): ApiResponse<HealthResponse> {
    return {
      code: "OK",
      message: "服务运行正常",
      data: this.healthService.getHealth(),
      traceId: randomUUID(),
    };
  }
}
