import { createRouter, createWebHistory } from "vue-router";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "dashboard",
      component: () => import("@/views/DashboardView.vue"),
      meta: {
        requiresAuth: true,
        permission: "dashboard.view",
      },
    },
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/LoginView.vue"),
      meta: { guestOnly: true },
    },
    {
      path: "/change-password",
      name: "change-password",
      component: () => import("@/views/ChangePasswordView.vue"),
      meta: { requiresAuth: true },
    },
    {
      path: "/forbidden",
      name: "forbidden",
      component: () => import("@/views/ForbiddenView.vue"),
      meta: { requiresAuth: true },
    },
    {
      path: "/admin",
      name: "admin-dashboard",
      component: () => import("@/views/admin/AdminDashboardView.vue"),
      meta: {
        requiresAuth: true,
        permission: "admin.user.manage",
      },
    },
    {
      path: "/admin/users",
      name: "admin-users",
      component: () => import("@/views/admin/UserManagementView.vue"),
      meta: {
        requiresAuth: true,
        permission: "admin.user.manage",
      },
    },
    {
      path: "/admin/roles",
      name: "admin-roles",
      component: () => import("@/views/admin/RolePermissionsView.vue"),
      meta: {
        requiresAuth: true,
        permission: "admin.role.manage",
      },
    },
    {
      path: "/admin/project-scopes",
      redirect: "/admin/users?focus=scopes",
      meta: {
        requiresAuth: true,
        permission: "admin.scope.manage",
      },
    },
    {
      path: "/admin/imports",
      name: "admin-imports",
      component: () => import("@/views/admin/ProjectImportView.vue"),
      meta: {
        requiresAuth: true,
        permission: "admin.import.manage",
      },
    },
    {
      path: "/admin/api-keys",
      name: "admin-api-keys",
      component: () => import("@/views/admin/ApiKeyManagementView.vue"),
      meta: {
        requiresAuth: true,
        permission: "admin.ai.manage",
      },
    },
    {
      path: "/admin/system-config",
      name: "admin-system-config",
      component: () => import("@/views/admin/SystemConfigView.vue"),
      meta: {
        requiresAuth: true,
        permission: "admin.config.manage",
      },
    },
    {
      path: "/admin/audit-logs",
      name: "admin-audit-logs",
      component: () => import("@/views/admin/AuditLogsView.vue"),
      meta: {
        requiresAuth: true,
        permission: "admin.audit.view",
      },
    },
    {
      path: "/mailbox-settings",
      name: "mailbox-settings",
      component: () => import("@/views/MailboxSettingsView.vue"),
      meta: {
        requiresAuth: true,
        permission: "mailbox.manage_self",
      },
    },
    {
      path: "/mail-sync-results",
      name: "mail-sync-results",
      component: () => import("@/views/MailSyncResultsView.vue"),
      meta: {
        requiresAuth: true,
        permission: "mailbox.sync_self",
      },
    },
  ],
});

router.beforeEach(async (to) => {
  const { useAuthStore } = await import("@/stores/auth");
  const auth = useAuthStore();
  await auth.restore();

  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return {
      path: "/login",
      query: { redirect: to.fullPath },
    };
  }
  if (
    auth.user?.mustChangePassword &&
    to.path !== "/change-password"
  ) {
    return "/change-password";
  }
  const permission = to.meta.permission;
  if (
    typeof permission === "string" &&
    !auth.user?.permissions.includes(permission)
  ) {
    return "/forbidden";
  }
  if (
    to.meta.guestOnly &&
    auth.isAuthenticated &&
    !auth.user?.mustChangePassword
  ) {
    return "/";
  }

  return true;
});
