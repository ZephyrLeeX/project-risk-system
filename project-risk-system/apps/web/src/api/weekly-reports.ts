import type { OpenApi } from "@risk-platform/contracts";

import { apiRequest } from "./http";

/**
 * Generated OpenAPI weekly-report contract types (ADR 0021 / T045 fidelity).
 *
 * These aliases only re-reference the frozen generated authority in
 * `packages/contracts/src/generated/openapi.ts`; they are never hand-written
 * substitutes and must not diverge from the generated surface.
 */
export type WeeklyReportResponse =
  OpenApi.components["schemas"]["WeeklyReportResponse"];
export type WeeklyProjectSummary =
  OpenApi.components["schemas"]["WeeklyProjectSummary"];
export type WeeklyProjectDetail =
  OpenApi.components["schemas"]["WeeklyProjectDetail"];
export type WeeklyReportItemResponse =
  OpenApi.components["schemas"]["WeeklyReportItemResponse"];
export type WeeklyProject = OpenApi.components["schemas"]["WeeklyProject"];

export const weeklyReportsApi = {
  /** Current Shanghai-week aggregate (`GET /weekly-reports/current`). */
  async current(): Promise<WeeklyReportResponse> {
    return (await apiRequest<WeeklyReportResponse>("/weekly-reports/current"))
      .data;
  },

  /** A specific week's aggregate (`GET /weekly-reports/{week_start}`). */
  async report(weekStart: string): Promise<WeeklyReportResponse> {
    return (
      await apiRequest<WeeklyReportResponse>(
        `/weekly-reports/${encodeURIComponent(weekStart)}`,
      )
    ).data;
  },

  /** Per-project risk items for a week (`GET /weekly-reports/{week_start}/projects/{project_id}`). */
  async detail(
    weekStart: string,
    projectId: string,
  ): Promise<WeeklyProjectDetail> {
    return (
      await apiRequest<WeeklyProjectDetail>(
        `/weekly-reports/${encodeURIComponent(weekStart)}/projects/${encodeURIComponent(projectId)}`,
      )
    ).data;
  },
};
