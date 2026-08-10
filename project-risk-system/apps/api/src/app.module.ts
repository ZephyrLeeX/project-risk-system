import { Module } from "@nestjs/common";
import { ConfigModule } from "@nestjs/config";
import { APP_GUARD } from "@nestjs/core";
import { resolve } from "node:path";

import { AdminModule } from "./admin/admin.module";
import { AiProvidersModule } from "./ai-providers/ai-providers.module";
import { AuditModule } from "./audit/audit.module";
import { AuthModule } from "./auth/auth.module";
import { CsrfOriginGuard } from "./auth/csrf-origin.guard";
import { HealthModule } from "./health/health.module";
import { ImportsModule } from "./imports/imports.module";
import { DashboardModule } from "./dashboard/dashboard.module";
import { validateEnvironment } from "./config/env.validation";
import { PrismaModule } from "./prisma/prisma.module";
import { RbacModule } from "./rbac/rbac.module";
import { RiskTimelineModule } from "./risk-timeline/risk-timeline.module";
import { TodosModule } from "./todos/todos.module";
import { SystemConfigModule } from "./system-config/system-config.module";
import { MailboxModule } from "./mailbox/mailbox.module";

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      cache: true,
      envFilePath: [
        resolve(process.cwd(), ".env"),
        resolve(process.cwd(), "../../.env"),
      ],
      validate: validateEnvironment,
    }),
    PrismaModule,
    RbacModule,
    RiskTimelineModule,
    AiProvidersModule,
    AdminModule,
    AuditModule,
    AuthModule,
    HealthModule,
    ImportsModule,
    DashboardModule,
    TodosModule,
    SystemConfigModule,
    MailboxModule,
  ],
  providers: [
    {
      provide: APP_GUARD,
      useClass: CsrfOriginGuard,
    },
  ],
})
export class AppModule {}
