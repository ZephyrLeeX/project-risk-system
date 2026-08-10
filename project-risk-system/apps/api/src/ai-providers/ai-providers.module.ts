import { Module } from "@nestjs/common";

import { AuthModule } from "../auth/auth.module";
import { AiConnectionService } from "./ai-connection.service";
import { AiProvidersController } from "./ai-providers.controller";
import { AiProvidersService } from "./ai-providers.service";
import { CredentialEncryptionService } from "./credential-encryption.service";

@Module({
  imports: [AuthModule],
  controllers: [AiProvidersController],
  providers: [
    AiProvidersService,
    AiConnectionService,
    CredentialEncryptionService,
  ],
  exports: [AiProvidersService, CredentialEncryptionService],
})
export class AiProvidersModule {}
