import type {
  AiCallLogDetail,
  AiCallLogListItem,
  AiCallResult,
  AiCallScene,
  AiConnectionTestRequest,
  AiConnectionTestResult,
  AiProviderListItem,
  AiProviderMutationRequest,
  AiProviderStatusFilter,
  AiProviderStrategyItem,
  AiProviderSummary,
  AiUsageOverview,
  CreateAiProviderRequest,
  PaginatedResponse,
  RotateAiProviderKeyRequest,
} from "@risk-platform/contracts";

import { apiRequest } from "./http";

function queryString(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const aiProviderApi = {
  async summary(): Promise<AiProviderSummary> {
    return (await apiRequest<AiProviderSummary>("/admin/ai-services/summary")).data;
  },

  async list(filters: { keyword?: string; status?: AiProviderStatusFilter } = {}): Promise<AiProviderListItem[]> {
    return (await apiRequest<AiProviderListItem[]>(`/admin/ai-services${queryString(filters)}`)).data;
  },

  async strategy(): Promise<AiProviderStrategyItem[]> {
    return (await apiRequest<AiProviderStrategyItem[]>("/admin/ai-services/strategy")).data;
  },

  async create(request: CreateAiProviderRequest): Promise<AiProviderListItem> {
    return (await apiRequest<AiProviderListItem>("/admin/ai-services", { method: "POST", body: JSON.stringify(request) })).data;
  },

  async update(id: string, request: AiProviderMutationRequest): Promise<AiProviderListItem> {
    return (await apiRequest<AiProviderListItem>(`/admin/ai-services/${id}`, { method: "PATCH", body: JSON.stringify(request) })).data;
  },

  async rotateKey(id: string, request: RotateAiProviderKeyRequest): Promise<AiProviderListItem> {
    return (await apiRequest<AiProviderListItem>(`/admin/ai-services/${id}/rotate-key`, { method: "POST", body: JSON.stringify(request) })).data;
  },

  async setDefault(id: string): Promise<AiProviderListItem> {
    return (await apiRequest<AiProviderListItem>(`/admin/ai-services/${id}/set-default`, { method: "POST" })).data;
  },

  async setStatus(id: string, enabled: boolean): Promise<AiProviderListItem> {
    return (await apiRequest<AiProviderListItem>(`/admin/ai-services/${id}/status`, { method: "POST", body: JSON.stringify({ enabled }) })).data;
  },

  async test(id: string): Promise<AiConnectionTestResult> {
    return (await apiRequest<AiConnectionTestResult>(`/admin/ai-services/${id}/test`, { method: "POST" })).data;
  },

  async testDraft(request: AiConnectionTestRequest): Promise<AiConnectionTestResult> {
    return (await apiRequest<AiConnectionTestResult>("/admin/ai-services/test-draft", { method: "POST", body: JSON.stringify(request) })).data;
  },

  async testAll(): Promise<AiConnectionTestResult[]> {
    return (await apiRequest<AiConnectionTestResult[]>("/admin/ai-services/test-all", { method: "POST" })).data;
  },

  async usage(scene?: AiCallScene): Promise<AiUsageOverview> {
    return (await apiRequest<AiUsageOverview>(`/admin/ai-services/usage${queryString({ scene })}`)).data;
  },

  async calls(filters: { page?: number; pageSize?: number; result?: AiCallResult; scene?: AiCallScene }): Promise<PaginatedResponse<AiCallLogListItem>> {
    return (await apiRequest<PaginatedResponse<AiCallLogListItem>>(`/admin/ai-services/calls${queryString(filters)}`)).data;
  },

  async callDetail(id: string): Promise<AiCallLogDetail> {
    return (await apiRequest<AiCallLogDetail>(`/admin/ai-services/calls/${id}`)).data;
  },
};
