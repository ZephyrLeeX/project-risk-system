import type {
  ConfirmProjectImportRequest,
  MatchSupplementalCollectionRequest,
  PaginatedResponse,
  ProjectImportBatchDetail,
  ProjectImportBatchSummary,
  ProjectOption,
} from "@risk-platform/contracts";

import { apiDownload, apiRequest } from "./http";

export const projectImportApi = {
  async preview(file: File): Promise<ProjectImportBatchDetail> {
    const form = new FormData();
    form.append("file", file);
    return (
      await apiRequest<ProjectImportBatchDetail>(
        "/imports/project-list/preview",
        {
          method: "POST",
          body: form,
        },
      )
    ).data;
  },

  async batches(
    page = 1,
    pageSize = 10,
  ): Promise<PaginatedResponse<ProjectImportBatchSummary>> {
    return (
      await apiRequest<PaginatedResponse<ProjectImportBatchSummary>>(
        `/imports/project-list/batches?page=${page}&pageSize=${pageSize}`,
      )
    ).data;
  },

  async detail(id: string): Promise<ProjectImportBatchDetail> {
    return (
      await apiRequest<ProjectImportBatchDetail>(
        `/imports/project-list/batches/${id}`,
      )
    ).data;
  },

  async downloadSource(id: string): Promise<string> {
    return apiDownload(`/imports/project-list/batches/${id}/source`);
  },

  async projectOptions(): Promise<ProjectOption[]> {
    return (
      await apiRequest<ProjectOption[]>(
        "/imports/project-list/projects/options",
      )
    ).data;
  },

  async matchSupplemental(
    rowId: string,
    request: MatchSupplementalCollectionRequest,
  ): Promise<ProjectImportBatchDetail> {
    return (
      await apiRequest<ProjectImportBatchDetail>(
        `/imports/project-list/supplemental/${rowId}/match`,
        {
          method: "POST",
          body: JSON.stringify(request),
        },
      )
    ).data;
  },

  async unmatchSupplemental(
    rowId: string,
  ): Promise<ProjectImportBatchDetail> {
    return (
      await apiRequest<ProjectImportBatchDetail>(
        `/imports/project-list/supplemental/${rowId}/match`,
        { method: "DELETE" },
      )
    ).data;
  },

  async confirm(
    id: string,
    request: ConfirmProjectImportRequest,
  ): Promise<ProjectImportBatchDetail> {
    return (
      await apiRequest<ProjectImportBatchDetail>(
        `/imports/project-list/batches/${id}/confirm`,
        {
          method: "POST",
          body: JSON.stringify(request),
        },
      )
    ).data;
  },

  async rollback(id: string): Promise<ProjectImportBatchDetail> {
    return (
      await apiRequest<ProjectImportBatchDetail>(
        `/imports/project-list/batches/${id}/rollback`,
        { method: "POST" },
      )
    ).data;
  },
};
