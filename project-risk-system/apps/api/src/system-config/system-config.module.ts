import { Global, Module } from "@nestjs/common";

import { AuthModule } from "../auth/auth.module";
import { SystemConfigController } from "./system-config.controller";
import { SystemConfigService } from "./system-config.service";

@Global()
@Module({
  imports: [AuthModule],
  controllers: [SystemConfigController],
  providers: [SystemConfigService],
  exports: [SystemConfigService],
})
export class SystemConfigModule {}
