import type {
  AuditActionGroup,
  AuditDateRange,
  AuditExportFormat,
  AuditLogDetail,
  AuditLogIntegrity,
  AuditLogListItem,
  AuditLogOptions,
  AuditLogResult,
  AuditLogSummary,
  AuditModuleKey,
  PaginatedResponse,
} from "@risk-platform/contracts";

import { apiDownloadRequest, apiRequest } from "./http";

export interface AuditLogFilters {
  keyword?: string;
  module?: AuditModuleKey;
  action?: AuditActionGroup;
  result?: AuditLogResult;
  dateRange?: AuditDateRange;
  startDate?: string;
  endDate?: string;
  sensitiveOnly?: boolean;
  page?: number;
  pageSize?: number;
}

function queryString(values: AuditLogFilters): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "" && value !== false) {
      params.set(key, String(value));
    }
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const auditLogsApi = {
  async summary(): Promise<AuditLogSummary> {
    return (await apiRequest<AuditLogSummary>("/admin/audit-logs/summary")).data;
  },

  async options(): Promise<AuditLogOptions> {
    return (await apiRequest<AuditLogOptions>("/admin/audit-logs/options")).data;
  },

  async integrity(): Promise<AuditLogIntegrity> {
    return (await apiRequest<AuditLogIntegrity>("/admin/audit-logs/integrity")).data;
  },

  async list(filters: AuditLogFilters): Promise<PaginatedResponse<AuditLogListItem>> {
    return (
      await apiRequest<PaginatedResponse<AuditLogListItem>>(
        `/admin/audit-logs${queryString(filters)}`,
      )
    ).data;
  },

  async detail(id: string): Promise<AuditLogDetail> {
    return (await apiRequest<AuditLogDetail>(`/admin/audit-logs/${id}`)).data;
  },

  async export(
    filters: AuditLogFilters,
    format: AuditExportFormat,
    reason: string,
  ): Promise<string> {
    const { page: _page, pageSize: _pageSize, ...scope } = filters;
    return apiDownloadRequest(
      "/admin/audit-logs/export",
      {
        method: "POST",
        body: JSON.stringify({ ...scope, format, reason }),
      },
      `审计日志.${format === "CSV" ? "csv" : "xlsx"}`,
    );
  },
};
