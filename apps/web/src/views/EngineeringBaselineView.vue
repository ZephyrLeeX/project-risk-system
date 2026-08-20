<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const auth = useAuthStore();
const foundations = [
  "真实账号密码登录",
  "HttpOnly 服务端会话",
  "首次登录强制修改密码",
  "连续失败自动锁定账号",
  "登录、改密与退出审计",
];
const adminLinks = computed(() =>
  [
    {
      to: "/admin/users",
      label: "进入用户管理",
      permission: "admin.user.manage",
    },
    {
      to: "/admin/roles",
      label: "进入角色权限",
      permission: "admin.role.manage",
    },
  ].filter((item) => auth.user?.permissions.includes(item.permission)),
);

async function logout(): Promise<void> {
  await auth.logout();
  await router.replace("/login");
}
</script>

<template>
  <main class="baseline-page">
    <section class="baseline-card" aria-labelledby="baseline-title">
      <p class="eyebrow">ENGINEERING BASELINE</p>
      <h1 id="baseline-title">项目风险管理平台</h1>
      <p class="summary">
        {{ auth.user?.displayName }}，认证闭环已经接入正式工程。
        本页暂作为第 2 步验收入口，后续将逐页承接现有 HTML 原型。
      </p>

      <ul class="foundation-list">
        <li v-for="item in foundations" :key="item">
          <span aria-hidden="true">✓</span>
          {{ item }}
        </li>
      </ul>

      <div class="status-row">
        <span class="status-dot" aria-hidden="true"></span>
        第一阶段 · 第2步
      </div>
      <div v-if="adminLinks.length" class="baseline-admin-links">
        <RouterLink
          v-for="link in adminLinks"
          :key="link.to"
          :to="link.to"
        >
          {{ link.label }}
        </RouterLink>
      </div>
      <button class="baseline-logout" type="button" @click="logout">
        安全退出
      </button>
    </section>
  </main>
</template>
