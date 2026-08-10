import { Module } from "@nestjs/common";

import { AiProvidersModule } from "../ai-providers/ai-providers.module";
import { AuthModule } from "../auth/auth.module";
import { MailboxConnectionService } from "./mailbox-connection.service";
import { MailboxController } from "./mailbox.controller";
import { MailContentParserService } from "./mail-content-parser.service";
import { MailProjectMatcherService } from "./mail-project-matcher.service";
import { MailRiskExtractorService } from "./mail-risk-extractor.service";
import { MailSyncProcessorService } from "./mail-sync-processor.service";
import { MailSyncResultsController } from "./mail-sync-results.controller";
import { MailSyncResultsService } from "./mail-sync-results.service";
import { MailboxService } from "./mailbox.service";
import { RiskTimelineModule } from "../risk-timeline/risk-timeline.module";

@Module({
  imports: [AuthModule, AiProvidersModule, RiskTimelineModule],
  controllers: [MailboxController, MailSyncResultsController],
  providers: [
    MailboxService,
    MailboxConnectionService,
    MailContentParserService,
    MailProjectMatcherService,
    MailRiskExtractorService,
    MailSyncProcessorService,
    MailSyncResultsService,
  ],
  exports: [MailboxService],
})
export class MailboxModule {}
