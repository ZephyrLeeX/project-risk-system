import type {
  MailboxConfigInput,
  MailboxConnectionTestResult,
  MailboxOverview,
  MailMessageDetail,
  MailMessageListResponse,
  MailMessageStatus,
  MailRiskCandidateItem,
  MailRiskCandidateUpdateInput,
  MailRiskReviewOptions,
  MailSyncBatchItem,
  MailSyncSummary,
  PaginatedResponse,
} from "@risk-platform/contracts";

import { apiRequest } from "./http";

export const mailboxApi = {
  async overview(): Promise<MailboxOverview> {
    return (await apiRequest<MailboxOverview>("/mailbox/me")).data;
  },

  async save(input: MailboxConfigInput): Promise<MailboxOverview> {
    return (
      await apiRequest<MailboxOverview>("/mailbox/me", {
        method: "PUT",
        body: JSON.stringify(input),
      })
    ).data;
  },

  async test(input: MailboxConfigInput): Promise<MailboxConnectionTestResult> {
    return (
      await apiRequest<MailboxConnectionTestResult>("/mailbox/me/test", {
        method: "POST",
        body: JSON.stringify(input),
      })
    ).data;
  },

  async setEnabled(enabled: boolean): Promise<MailboxOverview> {
    return (
      await apiRequest<MailboxOverview>("/mailbox/me/status", {
        method: "POST",
        body: JSON.stringify({ enabled }),
      })
    ).data;
  },

  async sync(): Promise<MailSyncBatchItem> {
    return (
      await apiRequest<MailSyncBatchItem>("/mailbox/me/sync", { method: "POST" })
    ).data;
  },

  async syncSummary(): Promise<MailSyncSummary> {
    return (await apiRequest<MailSyncSummary>("/mailbox/sync-summary")).data;
  },

  async reviewOptions(): Promise<MailRiskReviewOptions> {
    return (await apiRequest<MailRiskReviewOptions>("/mailbox/review-options")).data;
  },

  async messages(query: { keyword?: string; status?: MailMessageStatus; batchId?: string; withRisk?: boolean; page?: number; pageSize?: number } = {}): Promise<MailMessageListResponse> {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    const suffix = params.size ? `?${params.toString()}` : "";
    return (await apiRequest<MailMessageListResponse>(`/mailbox/messages${suffix}`)).data;
  },

  async message(id: string): Promise<MailMessageDetail> {
    return (await apiRequest<MailMessageDetail>(`/mailbox/messages/${id}`)).data;
  },

  async confirmProject(id: string, projectId: string): Promise<{ status: string }> {
    return (await apiRequest<{ status: string }>(`/mailbox/messages/${id}/project-resolution`, {
      method: "POST",
      body: JSON.stringify({ projectId }),
    })).data;
  },

  async retryMessage(id: string): Promise<MailSyncBatchItem> {
    return (await apiRequest<MailSyncBatchItem>(`/mailbox/messages/${id}/retry`, { method: "POST" })).data;
  },

  async batches(page = 1, pageSize = 10): Promise<PaginatedResponse<MailSyncBatchItem>> {
    return (await apiRequest<PaginatedResponse<MailSyncBatchItem>>(`/mailbox/sync-batches?page=${page}&pageSize=${pageSize}`)).data;
  },

  async updateRiskCandidate(id: string, input: MailRiskCandidateUpdateInput): Promise<MailRiskCandidateItem> {
    return (await apiRequest<MailRiskCandidateItem>(`/mailbox/risk-candidates/${id}`, { method: "PATCH", body: JSON.stringify(input) })).data;
  },

  async ignoreRiskCandidate(id: string): Promise<MailRiskCandidateItem> {
    return (await apiRequest<MailRiskCandidateItem>(`/mailbox/risk-candidates/${id}/ignore`, { method: "POST" })).data;
  },

  async confirmRiskCandidate(id: string): Promise<MailRiskCandidateItem> {
    return (await apiRequest<MailRiskCandidateItem>(`/mailbox/risk-candidates/${id}/confirm`, { method: "POST" })).data;
  },
};
