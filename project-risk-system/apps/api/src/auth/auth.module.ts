import { Module } from "@nestjs/common";

import { AuthController } from "./auth.controller";
import { AuthSessionGuard } from "./auth-session.guard";
import { AuthService } from "./auth.service";

@Module({
  controllers: [AuthController],
  providers: [AuthService, AuthSessionGuard],
  exports: [AuthService, AuthSessionGuard],
})
export class AuthModule {}
