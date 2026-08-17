import type {
  ProjectOption,
  PublishSystemConfigRequest,
  SystemConfigModule,
  SystemConfigOverview,
  SystemConfigReleaseDetail,
  SystemConfigReleaseItem,
} from "@risk-platform/contracts";

import { apiRequest } from "./http";
import { requireSystemConfigOverview } from "./system-config-contract";

export const systemConfigApi = {
  async overview(): Promise<SystemConfigOverview> {
    return requireSystemConfigOverview(
      (await apiRequest<unknown>("/admin/system-config")).data,
    );
  },

  async projectOptions(): Promise<ProjectOption[]> {
    return (
      await apiRequest<ProjectOption[]>("/admin/system-config/project-options")
    ).data;
  },

  async publish(request: PublishSystemConfigRequest): Promise<SystemConfigOverview> {
    const body = {
      ...request,
      categories: request.categories.map(({ riskCount: _riskCount, ...category }) => category),
      aliases: request.aliases.map(
        ({
          projectName: _projectName,
          projectCode: _projectCode,
          projectOwnerName: _projectOwnerName,
          hitCount: _hitCount,
          lastHitAt: _lastHitAt,
          ...alias
        }) => alias,
      ),
    };
    return requireSystemConfigOverview(
      (
        await apiRequest<unknown>("/admin/system-config/publish", {
        method: "POST",
        body: JSON.stringify(body),
        })
      ).data,
    );
  },

  async releases(module: SystemConfigModule | "all" = "all"): Promise<SystemConfigReleaseItem[]> {
    const query = module === "all" ? "" : `?module=${module}`;
    return (
      await apiRequest<SystemConfigReleaseItem[]>(`/admin/system-config/releases${query}`)
    ).data;
  },

  async releaseDetail(id: string): Promise<SystemConfigReleaseDetail> {
    return (
      await apiRequest<SystemConfigReleaseDetail>(`/admin/system-config/releases/${id}`)
    ).data;
  },
};
