import type { OpenApi } from "@risk-platform/contracts";

import { apiRequest } from "./http";

type Schemas = OpenApi.components["schemas"];
export type ProviderAccount = Schemas["ProviderAccountResponse"];
export type ModelConfig = Schemas["ModelConfigResponse"];
export type ConnectionResult = Schemas["ProviderV2ConnectionResult"];
export type CreateAccountRequest = Schemas["CreateProviderAccountRequest"];
export type UpdateAccountRequest = Schemas["UpdateProviderAccountRequest"];
export type RotateKeyRequest = Schemas["RotateProviderAccountKeyRequest"];
export type CreateModelRequest = Schemas["CreateModelConfigRequest"];
export type UpdateModelRequest = Schemas["UpdateModelConfigRequest"];

export const aiProviderApi = {
  async accounts(): Promise<ProviderAccount[]> {
    return (await apiRequest<ProviderAccount[]>("/admin/ai-provider-v2/accounts")).data;
  },
  async createAccount(body: CreateAccountRequest): Promise<ProviderAccount> {
    return (await apiRequest<ProviderAccount>("/admin/ai-provider-v2/accounts", { method: "POST", body: JSON.stringify(body) })).data;
  },
  async updateAccount(id: string, body: UpdateAccountRequest): Promise<ProviderAccount> {
    return (await apiRequest<ProviderAccount>(`/admin/ai-provider-v2/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) })).data;
  },
  async rotateKey(id: string, body: RotateKeyRequest): Promise<ProviderAccount> {
    return (await apiRequest<ProviderAccount>(`/admin/ai-provider-v2/accounts/${id}/rotate-key`, { method: "POST", body: JSON.stringify(body) })).data;
  },
  async setAccountStatus(id: string, enabled: boolean): Promise<ProviderAccount> {
    return (await apiRequest<ProviderAccount>(`/admin/ai-provider-v2/accounts/${id}/status`, { method: "POST", body: JSON.stringify({ enabled }) })).data;
  },
  async testAccount(id: string): Promise<ConnectionResult> {
    return (await apiRequest<ConnectionResult>(`/admin/ai-provider-v2/accounts/${id}/test`, { method: "POST" })).data;
  },
  async discoverModels(id: string): Promise<Schemas["DiscoveredModelResponse"][]> {
    return (await apiRequest<Schemas["DiscoveredModelResponse"][]>(`/admin/ai-provider-v2/accounts/${id}/models/discover`)).data;
  },
  async models(accountId: string): Promise<ModelConfig[]> {
    return (await apiRequest<ModelConfig[]>(`/admin/ai-provider-v2/accounts/${accountId}/models`)).data;
  },
  async createModel(accountId: string, body: CreateModelRequest): Promise<ModelConfig> {
    return (await apiRequest<ModelConfig>(`/admin/ai-provider-v2/accounts/${accountId}/models`, { method: "POST", body: JSON.stringify(body) })).data;
  },
  async updateModel(accountId: string, modelId: string, body: UpdateModelRequest): Promise<ModelConfig> {
    return (await apiRequest<ModelConfig>(`/admin/ai-provider-v2/accounts/${accountId}/models/${modelId}`, { method: "PATCH", body: JSON.stringify(body) })).data;
  },
  async setModelStatus(accountId: string, modelId: string, enabled: boolean): Promise<ModelConfig> {
    return (await apiRequest<ModelConfig>(`/admin/ai-provider-v2/accounts/${accountId}/models/${modelId}/status`, { method: "POST", body: JSON.stringify({ enabled }) })).data;
  },
  async setDefaultModel(accountId: string, modelId: string): Promise<ModelConfig> {
    return (await apiRequest<ModelConfig>(`/admin/ai-provider-v2/accounts/${accountId}/models/${modelId}/set-default`, { method: "POST" })).data;
  },
  async testModel(accountId: string, modelId: string): Promise<ConnectionResult> {
    return (await apiRequest<ConnectionResult>(`/admin/ai-provider-v2/accounts/${accountId}/models/${modelId}/test`, { method: "POST" })).data;
  },
};
