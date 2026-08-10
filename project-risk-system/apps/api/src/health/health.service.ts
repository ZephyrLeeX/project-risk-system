import { Injectable } from "@nestjs/common";

import type { HealthResponse } from "@risk-platform/contracts";

@Injectable()
export class HealthService {
  getHealth(): HealthResponse {
    return {
      service: "project-risk-api",
      status: "ok",
      version: "0.1.0",
      timestamp: new Date().toISOString(),
    };
  }
}
