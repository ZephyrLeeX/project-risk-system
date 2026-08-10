import { Global, Module } from "@nestjs/common";

import { DataScopeService } from "./data-scope.service";
import { PermissionGuard } from "./permission.guard";

@Global()
@Module({
  providers: [PermissionGuard, DataScopeService],
  exports: [PermissionGuard, DataScopeService],
})
export class RbacModule {}
