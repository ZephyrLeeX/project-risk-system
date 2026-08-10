import { Global, Module } from "@nestjs/common";

import { AuditService } from "./audit.service";
import { AuditController } from "./audit.controller";
import { AuthModule } from "../auth/auth.module";

@Global()
@Module({
  imports: [AuthModule],
  controllers: [AuditController],
  providers: [AuditService],
  exports: [AuditService],
})
export class AuditModule {}
