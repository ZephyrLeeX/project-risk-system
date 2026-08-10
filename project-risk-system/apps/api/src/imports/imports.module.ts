import { Module } from "@nestjs/common";

import { AuthModule } from "../auth/auth.module";
import { ProjectImportController } from "./project-import.controller";
import { ProjectImportService } from "./project-import.service";
import { ProjectListParser } from "./project-list.parser";

@Module({
  imports: [AuthModule],
  controllers: [ProjectImportController],
  providers: [ProjectImportService, ProjectListParser],
})
export class ImportsModule {}
