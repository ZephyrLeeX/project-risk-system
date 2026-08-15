import type {
  DepartmentCollectionDetail,
  DepartmentCollectionSummary,
  DashboardFocusItem,
  DashboardRiskDetail,
  DashboardRiskFilterOptions,
  DashboardRiskListResponse,
  DashboardSummary,
  ManagerTodoDetail,
  ManagerTodoListResponse,
  ProjectRiskLevel,
  ReopenRiskRequest,
  ResolveRiskRequest,
  ResolvedRiskListResponse,
  RiskCollectionDetail,
  RiskCollectionListResponse,
  RiskTimelineDetail,
  RiskTimelineEventType,
  RiskTimelineListResponse,
  RiskSourceType,
  UpdateManagerTodoRequest,
} from "@risk-platform/contracts";

import { apiRequest } from "./http";

export interface DashboardRiskQuery {
  keyword?: string;
  level?: ProjectRiskLevel | "";
  categoryId?: string;
  owner?: string;
  sourceType?: RiskSourceType | "";
  page?: number;
  pageSize?: number;
}

export type ResolvedRiskQuery = DashboardRiskQuery;

export interface ManagerTodoQuery {
  owner?: string;
  status?: "PENDING" | "IN_PROGRESS" | "COMPLETED" | "";
  page?: number;
  pageSize?: number;
}

export interface RiskCollectionQuery {
  keyword?: string;
  level?: ProjectRiskLevel | "";
  owner?: string;
}

export interface RiskTimelineQuery {
  keyword?: string;
  level?: ProjectRiskLevel | "";
  eventType?: RiskTimelineEventType | "";
  projectId?: string;
  page?: number;
  pageSize?: number;
}

function queryString(query: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  const value = params.toString();
  return value ? `?${value}` : "";
}

export const dashboardApi = {
  async summary(): Promise<DashboardSummary> {
    return (await apiRequest<DashboardSummary>("/dashboard/summary")).data;
  },

  async focus(): Promise<DashboardFocusItem[]> {
    return (await apiRequest<DashboardFocusItem[]>("/dashboard/focus")).data;
  },

  async departmentCollections(): Promise<DepartmentCollectionSummary> {
    return (
      await apiRequest<DepartmentCollectionSummary>(
        "/dashboard/departments/collections",
      )
    ).data;
  },

  async departmentCollectionDetail(
    departmentKey: string,
  ): Promise<DepartmentCollectionDetail> {
    return (
      await apiRequest<DepartmentCollectionDetail>(
        `/dashboard/departments/${encodeURIComponent(departmentKey)}/collections`,
      )
    ).data;
  },

  async riskCollections(
    query: RiskCollectionQuery,
  ): Promise<RiskCollectionListResponse> {
    return (
      await apiRequest<RiskCollectionListResponse>(
        `/dashboard/collections${queryString(query)}`,
      )
    ).data;
  },

  async riskCollectionDetail(
    projectId: string,
  ): Promise<RiskCollectionDetail> {
    return (
      await apiRequest<RiskCollectionDetail>(
        `/dashboard/collections/${encodeURIComponent(projectId)}`,
      )
    ).data;
  },

  async riskTimeline(
    query: RiskTimelineQuery,
  ): Promise<RiskTimelineListResponse> {
    return (
      await apiRequest<RiskTimelineListResponse>(
        `/dashboard/timeline${queryString(query)}`,
      )
    ).data;
  },

  async riskTimelineDetail(
    id: string,
  ): Promise<RiskTimelineDetail> {
    return (
      await apiRequest<RiskTimelineDetail>(
        `/dashboard/timeline/${encodeURIComponent(id)}`,
      )
    ).data;
  },

  async riskOptions(): Promise<DashboardRiskFilterOptions> {
    return (
      await apiRequest<DashboardRiskFilterOptions>("/risks/options")
    ).data;
  },

  async risks(
    query: DashboardRiskQuery,
  ): Promise<DashboardRiskListResponse> {
    return (
      await apiRequest<DashboardRiskListResponse>(
        `/risks${queryString(query)}`,
      )
    ).data;
  },

  async riskDetail(id: string): Promise<DashboardRiskDetail> {
    return (
      await apiRequest<DashboardRiskDetail>(`/risks/${id}`)
    ).data;
  },

  async resolvedRisks(
    query: ResolvedRiskQuery,
  ): Promise<ResolvedRiskListResponse> {
    return (
      await apiRequest<ResolvedRiskListResponse>(
        `/risks/resolved${queryString(query)}`,
      )
    ).data;
  },

  async resolveRisk(
    id: string,
    request: ResolveRiskRequest,
  ): Promise<DashboardRiskDetail> {
    return (
      await apiRequest<DashboardRiskDetail>(`/risks/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify(request),
      })
    ).data;
  },

  async reopenRisk(
    id: string,
    request: ReopenRiskRequest,
  ): Promise<DashboardRiskDetail> {
    return (
      await apiRequest<DashboardRiskDetail>(`/risks/${id}/reopen`, {
        method: "POST",
        body: JSON.stringify(request),
      })
    ).data;
  },

  async managerTodos(
    query: ManagerTodoQuery,
  ): Promise<ManagerTodoListResponse> {
    return (
      await apiRequest<ManagerTodoListResponse>(
        `/todos${queryString(query)}`,
      )
    ).data;
  },

  async managerTodoDetail(id: string): Promise<ManagerTodoDetail> {
    return (await apiRequest<ManagerTodoDetail>(`/todos/${id}`)).data;
  },

  async updateManagerTodo(
    id: string,
    request: UpdateManagerTodoRequest,
  ): Promise<ManagerTodoDetail> {
    return (
      await apiRequest<ManagerTodoDetail>(`/todos/${id}`, {
        method: "PATCH",
        body: JSON.stringify(request),
      })
    ).data;
  },
};
