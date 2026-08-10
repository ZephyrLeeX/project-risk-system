import { Module } from "@nestjs/common";

import { AuthModule } from "../auth/auth.module";
import { AdminController } from "./admin.controller";
import { AdminOptionsService } from "./admin-options.service";
import { RolesService } from "./roles.service";
import { UsersService } from "./users.service";

@Module({
  imports: [AuthModule],
  controllers: [AdminController],
  providers: [UsersService, RolesService, AdminOptionsService],
})
export class AdminModule {}
