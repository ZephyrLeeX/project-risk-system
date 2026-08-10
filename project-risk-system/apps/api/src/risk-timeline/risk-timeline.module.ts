import { Global, Module } from "@nestjs/common";

import { RiskTimelineService } from "./risk-timeline.service";

@Global()
@Module({
  providers: [RiskTimelineService],
  exports: [RiskTimelineService],
})
export class RiskTimelineModule {}
