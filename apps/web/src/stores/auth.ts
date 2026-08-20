import type {
  AuthenticatedUser,
  ChangePasswordRequest,
  LoginRequest,
} from "@risk-platform/contracts";
import { defineStore } from "pinia";

import { authApi } from "@/api/auth";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null as AuthenticatedUser | null,
    expiresAt: null as string | null,
    restored: false,
  }),

  getters: {
    isAuthenticated: (state) => Boolean(state.user),
  },

  actions: {
    async login(request: LoginRequest): Promise<AuthenticatedUser> {
      const response = await authApi.login(request);
      this.user = response.user;
      this.expiresAt = response.expiresAt;
      this.restored = true;
      return response.user;
    },

    async restore(): Promise<void> {
      if (this.restored) {
        return;
      }
      try {
        const response = await authApi.getSession();
        this.user = response.user;
        this.expiresAt = response.expiresAt;
      } catch {
        this.clear();
      } finally {
        this.restored = true;
      }
    },

    async changePassword(request: ChangePasswordRequest): Promise<void> {
      await authApi.changePassword(request);
      this.clear();
      this.restored = true;
    },

    async logout(): Promise<void> {
      try {
        await authApi.logout();
      } finally {
        this.clear();
        this.restored = true;
      }
    },

    clear(): void {
      this.user = null;
      this.expiresAt = null;
    },
  },
});
