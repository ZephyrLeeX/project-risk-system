import type {
  AdminUserListItem,
  AdminUserSummary,
  DepartmentOption,
  PaginatedResponse,
  PermissionItem,
  ProjectOption,
  RoleListItem,
  RoleMutationRequest,
  UserAuditRecord,
  UserMutationRequest,
  UserMutationResponse,
} from "@risk-platform/contracts";

import { apiRequest } from "./http";

export interface UserFilters {
  page?: number;
  pageSize?: number;
  keyword?: string;
  roleCode?: string;
  status?: string;
  departmentId?: string;
}

function toQuery(filters: UserFilters): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const adminApi = {
  async userSummary(): Promise<AdminUserSummary> {
    return (await apiRequest<AdminUserSummary>("/admin/users/summary")).data;
  },

  async users(
    filters: UserFilters,
  ): Promise<PaginatedResponse<AdminUserListItem>> {
    return (
      await apiRequest<PaginatedResponse<AdminUserListItem>>(
        `/admin/users${toQuery(filters)}`,
      )
    ).data;
  },

  async user(id: string): Promise<AdminUserListItem> {
    return (await apiRequest<AdminUserListItem>(`/admin/users/${id}`)).data;
  },

  async createUser(
    request: UserMutationRequest,
  ): Promise<UserMutationResponse> {
    return (
      await apiRequest<UserMutationResponse>("/admin/users", {
        method: "POST",
        body: JSON.stringify(request),
      })
    ).data;
  },

  async updateUser(
    id: string,
    request: UserMutationRequest,
  ): Promise<UserMutationResponse> {
    return (
      await apiRequest<UserMutationResponse>(`/admin/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(request),
      })
    ).data;
  },

  async setUserStatus(
    id: string,
    status: "ACTIVE" | "DISABLED",
  ): Promise<AdminUserListItem> {
    return (
      await apiRequest<AdminUserListItem>(`/admin/users/${id}/status`, {
        method: "POST",
        body: JSON.stringify({ status }),
      })
    ).data;
  },

  async unlockUser(id: string): Promise<AdminUserListItem> {
    return (
      await apiRequest<AdminUserListItem>(`/admin/users/${id}/unlock`, {
        method: "POST",
      })
    ).data;
  },

  async resetPassword(id: string): Promise<{ initialPassword: string }> {
    return (
      await apiRequest<{ initialPassword: string }>(
        `/admin/users/${id}/reset-password`,
        { method: "POST" },
      )
    ).data;
  },

  async userRecords(id: string): Promise<UserAuditRecord[]> {
    return (
      await apiRequest<UserAuditRecord[]>(`/admin/users/${id}/records`)
    ).data;
  },

  async roles(): Promise<RoleListItem[]> {
    return (await apiRequest<RoleListItem[]>("/admin/roles")).data;
  },

  async createRole(request: RoleMutationRequest): Promise<RoleListItem> {
    return (
      await apiRequest<RoleListItem>("/admin/roles", {
        method: "POST",
        body: JSON.stringify(request),
      })
    ).data;
  },

  async updateRole(
    role: RoleListItem,
    request: RoleMutationRequest,
  ): Promise<RoleListItem> {
    const { code: _code, ...body } = request;
    return (
      await apiRequest<RoleListItem>(`/admin/roles/${role.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      })
    ).data;
  },

  async deleteRole(id: string): Promise<void> {
    await apiRequest<null>(`/admin/roles/${id}`, { method: "DELETE" });
  },

  async permissions(): Promise<PermissionItem[]> {
    return (await apiRequest<PermissionItem[]>("/admin/permissions")).data;
  },

  async departments(): Promise<DepartmentOption[]> {
    return (await apiRequest<DepartmentOption[]>("/admin/departments")).data;
  },

  async projects(): Promise<ProjectOption[]> {
    return (await apiRequest<ProjectOption[]>("/admin/projects/options")).data;
  },
};
