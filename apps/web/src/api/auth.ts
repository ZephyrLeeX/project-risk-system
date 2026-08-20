import type {
  ChangePasswordRequest,
  LoginRequest,
  LoginResponse,
  SessionResponse,
} from "@risk-platform/contracts";

import { apiRequest } from "./http";

export const authApi = {
  async login(request: LoginRequest): Promise<LoginResponse> {
    return (
      await apiRequest<LoginResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify(request),
      })
    ).data;
  },

  async getSession(): Promise<SessionResponse> {
    return (await apiRequest<SessionResponse>("/auth/session")).data;
  },

  async changePassword(
    request: ChangePasswordRequest,
  ): Promise<void> {
    await apiRequest<{ reloginRequired: true }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  async logout(): Promise<void> {
    await apiRequest<null>("/auth/logout", {
      method: "POST",
    });
  },
};
