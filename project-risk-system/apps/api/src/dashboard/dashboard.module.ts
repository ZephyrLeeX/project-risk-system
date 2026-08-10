import { Module } from "@nestjs/common";

import { AuthModule } from "../auth/auth.module";
import { DashboardController } from "./dashboard.controller";
import { DashboardService } from "./dashboard.service";
import { RiskLifecycleService } from "./risk-lifecycle.service";

@Module({
  imports: [AuthModule],
  controllers: [DashboardController],
  providers: [DashboardService, RiskLifecycleService],
})
export class DashboardModule {}
