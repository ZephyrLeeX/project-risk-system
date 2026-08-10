<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const mobileMenuOpen = ref(false);
const profileMenuOpen = ref(false);

const pageTitle = computed(() => {
  const titles: Record<string, string> = {
    "/admin": "管理概览",
    "/admin/users": "用户管理",
    "/admin/roles": "角色权限",
    "/admin/imports": "项目数据导入",
    "/admin/api-keys": "API Key 管理",
    "/admin/system-config": "系统配置",
    "/admin/audit-logs": "审计日志",
  };
  return titles[route.path] ?? "后台管理";
});

const navigationGroups = computed(() => [
  {
    label: "工作台",
    items: [
      {
        to: "/admin",
        label: "管理概览",
        icon: "icon-overview",
        permission: "admin.user.manage",
      },
    ],
  },
  {
    label: "组织与权限",
    items: [
      {
        to: "/admin/users",
        label: "用户管理",
        icon: "icon-users",
        permission: "admin.user.manage",
      },
      {
        to: "/admin/roles",
        label: "角色权限",
        icon: "icon-role",
        permission: "admin.role.manage",
      },
    ],
  },
  {
    label: "数据与服务",
    items: [
      {
        to: "/admin/imports",
        label: "项目数据导入",
        icon: "icon-import",
        permission: "admin.import.manage",
      },
      {
        to: "/admin/api-keys",
        label: "API Key 管理",
        icon: "icon-key",
        permission: "admin.ai.manage",
      },
    ],
  },
  {
    label: "系统治理",
    items: [
      {
        to: "/admin/system-config",
        label: "系统配置",
        icon: "icon-setting",
        permission: "admin.config.manage",
      },
      {
        to: "/admin/audit-logs",
        label: "审计日志",
        icon: "icon-audit",
        permission: "admin.audit.view",
      },
    ],
  },
]);

function canSee(permission: string): boolean {
  return auth.user?.permissions.includes(permission) ?? false;
}

async function logout(): Promise<void> {
  profileMenuOpen.value = false;
  await auth.logout();
  await router.replace("/login");
}
</script>

<template>
  <div class="admin-app prototype-admin-shell">
    <aside
      class="admin-sidebar"
      :class="{ 'is-open': mobileMenuOpen }"
      aria-label="后台管理导航"
    >
      <div class="sidebar-brand">
        <RouterLink class="admin-brand" to="/admin" aria-label="项目风险管理后台首页">
          <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
          <span class="brand-copy">
            <strong>项目风险管理</strong>
            <small>ADMIN CONSOLE</small>
          </span>
        </RouterLink>
        <span class="console-badge">管理后台</span>
      </div>

      <nav class="sidebar-nav">
        <template v-for="group in navigationGroups" :key="group.label">
          <p class="nav-group-label">{{ group.label }}</p>
          <template v-for="item in group.items" :key="`${group.label}-${item.label}`">
            <RouterLink
              v-if="canSee(item.permission)"
              :to="item.to"
              class="side-nav-item"
              :class="{ 'is-active': route.path === item.to }"
              @click="mobileMenuOpen = false"
            >
              <span class="side-icon" :class="item.icon" aria-hidden="true"></span>
              <span>{{ item.label }}</span>
            </RouterLink>
          </template>
        </template>
      </nav>

      <div class="sidebar-footer">
        <RouterLink class="back-business" to="/">
          <span aria-hidden="true"></span>
          返回风险看板
        </RouterLink>
        <div class="environment">
          <span aria-hidden="true"></span>
          <div>
            <strong>系统运行正常</strong>
            <small>V2.0 · 生产环境</small>
          </div>
        </div>
      </div>
    </aside>

    <button
      v-if="mobileMenuOpen"
      class="sidebar-backdrop"
      type="button"
      aria-label="关闭导航"
      @click="mobileMenuOpen = false"
    ></button>

    <div class="admin-workspace">
      <header class="admin-topbar">
        <div class="topbar-left">
          <button
            class="sidebar-toggle"
            type="button"
            aria-label="打开导航"
            @click="mobileMenuOpen = true"
          >
            <span></span><span></span><span></span>
          </button>
          <div class="breadcrumb" aria-label="当前位置">
            <span>后台管理</span><i aria-hidden="true"></i><strong>{{ pageTitle }}</strong>
          </div>
        </div>
        <div class="topbar-tools">
          <div class="secure-status"><span aria-hidden="true"></span>安全连接</div>
          <button class="top-icon-button" type="button" aria-label="查看后台通知">
            <span class="bell-icon" aria-hidden="true"></span><b>2</b>
          </button>
          <button
            class="admin-profile"
            type="button"
            :aria-expanded="profileMenuOpen"
            @click="profileMenuOpen = !profileMenuOpen"
          >
            <span class="admin-avatar" aria-hidden="true">{{ auth.user?.displayName.slice(0, 1) }}</span>
            <span><strong>{{ auth.user?.displayName }}</strong><small>全部项目</small></span>
            <i aria-hidden="true"></i>
          </button>
          <div v-if="profileMenuOpen" class="admin-profile-menu">
            <RouterLink to="/change-password" @click="profileMenuOpen = false">账号安全</RouterLink>
            <RouterLink to="/" @click="profileMenuOpen = false">返回业务端</RouterLink>
            <button type="button" @click="logout">退出登录</button>
          </div>
        </div>
      </header>

      <main class="admin-main"><slot /></main>
    </div>
  </div>
</template>
